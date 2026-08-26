"""Exact Runtime observation and hard-deadline subprocess execution."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator

from cernora_reference_workflow.common import canonical_content_id, sha256_bytes
from cernora_reference_workflow.controlled_experiment_spec import (
    ControlledExperimentSpecV2,
    Digest,
    StrictV2Contract,
)

Clock = Callable[[], float]
MAX_CAPTURE_BYTES = 1_048_576


class RuntimeAuthorityObservation(StrictV2Contract):
    """Observed immutable inputs required to match one Experiment exactly."""

    schema_version: Literal["cernora.reference.runtime-authority-observation/v1"]
    observation_id: Digest
    experiment_id: Digest
    runtime_configuration_sha256: Digest
    prompt_sha256: Digest
    instruction_sha256: Digest
    task_source_sha256: Digest
    task_image_sha256: Digest

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"observation_id"})
        )
        if self.observation_id != expected:
            raise ValueError("Runtime authority observation identity mismatch")
        return self

    def verify(self, spec: ControlledExperimentSpecV2) -> None:
        expected_image_digest = spec.container.image.rsplit("@sha256:", 1)[1]
        expected = (
            spec.experiment_id,
            spec.runtime.configuration_sha256,
            spec.prompt_sha256,
            spec.instruction_sha256,
            spec.task.task_source.source_sha256,
            expected_image_digest,
        )
        actual = (
            self.experiment_id,
            self.runtime_configuration_sha256,
            self.prompt_sha256,
            self.instruction_sha256,
            self.task_source_sha256,
            self.task_image_sha256,
        )
        if actual != expected:
            raise ValueError("Runtime observation does not equal the selected Experiment authority")


def observe_runtime_authority(spec: ControlledExperimentSpecV2) -> RuntimeAuthorityObservation:
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.runtime-authority-observation/v1",
        "experiment_id": spec.experiment_id,
        "runtime_configuration_sha256": spec.runtime.configuration_sha256,
        "prompt_sha256": spec.prompt_sha256,
        "instruction_sha256": spec.instruction_sha256,
        "task_source_sha256": spec.task.task_source.source_sha256,
        "task_image_sha256": spec.container.image.rsplit("@sha256:", 1)[1],
    }
    payload["observation_id"] = canonical_content_id(payload, excluded=frozenset())
    return RuntimeAuthorityObservation.model_validate(payload)


@dataclass(frozen=True)
class SubprocessResult:
    status: Literal["exited", "timed_out", "start_failure"]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    started_monotonic: float
    finished_monotonic: float
    receipt_sha256: str


def _bounded(payload: bytes) -> bytes:
    if len(payload) > MAX_CAPTURE_BYTES:
        raise ValueError("controlled subprocess output exceeds the capture limit")
    return payload


def run_subprocess_until(
    command: tuple[str, ...],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    deadline_monotonic: float,
    timeout_seconds: int,
    clock: Clock = time.monotonic,
) -> SubprocessResult:
    """Run one process group and terminate it at the earlier frozen deadline."""

    if not command:
        raise ValueError("controlled subprocess command must be non-empty")
    started = clock()
    available = min(float(timeout_seconds), deadline_monotonic - started)
    if available <= 0:
        raise TimeoutError("controlled subprocess deadline elapsed before start")
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        finished = clock()
        receipt = canonical_content_id(
            {
                "command": list(command),
                "error": type(exc).__name__,
                "status": "start_failure",
            },
            excluded=frozenset(),
        )
        return SubprocessResult(
            status="start_failure",
            exit_code=None,
            stdout=b"",
            stderr=b"",
            started_monotonic=started,
            finished_monotonic=finished,
            receipt_sha256=receipt,
        )
    status: Literal["exited", "timed_out", "start_failure"] = "exited"
    try:
        stdout, stderr = process.communicate(timeout=available)
    except subprocess.TimeoutExpired:
        status = "timed_out"
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
    finished = clock()
    stdout = _bounded(stdout)
    stderr = _bounded(stderr)
    exit_code = process.returncode if status == "exited" else None
    receipt = canonical_content_id(
        {
            "command": list(command),
            "exit_code": exit_code,
            "status": status,
            "stderr_sha256": sha256_bytes(stderr),
            "stdout_sha256": sha256_bytes(stdout),
        },
        excluded=frozenset(),
    )
    return SubprocessResult(
        status=status,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_monotonic=started,
        finished_monotonic=finished,
        receipt_sha256=receipt,
    )


__all__ = [
    "MAX_CAPTURE_BYTES",
    "RuntimeAuthorityObservation",
    "SubprocessResult",
    "observe_runtime_authority",
    "run_subprocess_until",
]
