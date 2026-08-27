"""Exact Runtime observation and hard-deadline subprocess execution."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator

from cernora_reference_workflow.common import (
    canonical_content_id,
    canonical_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    ControlledExperimentSpecV2,
    Digest,
    StrictV2Contract,
)

Clock = Callable[[], float]
MAX_CAPTURE_BYTES = 1_048_576


def runtime_invocation_sha256(spec: ControlledExperimentSpecV2) -> str:
    """Digest the path- and proxy-value-free semantic Harbor argv projection."""

    return sha256_bytes(
        canonical_json_bytes(
            {
                "agent": "cernora_reference_workflow.runtime_agent:TelemetryDisabledCodex",
                "agent_kwargs": {
                    "reasoning_effort": spec.runtime.reasoning_effort,
                    "reasoning_summary": "none",
                    "strict_config": True,
                    "version": spec.runtime.version,
                    "web_search": "disabled",
                },
                "auto_confirm": True,
                "delete_environment": True,
                "environment": "docker",
                "max_retries": 0,
                "model": spec.runtime.model,
                "n_attempts": 1,
                "n_concurrent": 1,
                "override_cpus": spec.limits.cpu_millis // 1000,
                "override_memory_mb": spec.limits.memory_mebibytes,
                "proxy_variables": ["ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"],
                "task_authority_sha256": spec.task.task_source.source_sha256,
            }
        )
    )


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
    invocation_sha256: Digest
    runtime_version_sha256: Digest
    model_sha256: Digest
    tool_schema_sha256: Digest
    generation_configuration_sha256: Digest
    timeout_sha256: Digest
    resources_sha256: Digest
    retry_policy_sha256: Digest
    dataset_sha256: Digest
    profile_sha256: Digest
    evaluation_policy_sha256: Digest
    report_contract_sha256: Digest
    statistical_plan_sha256: Digest

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
        projection = spec.core_projection()
        expected = (
            spec.experiment_id,
            spec.runtime.configuration_sha256,
            spec.prompt_sha256,
            spec.instruction_sha256,
            spec.task.task_source.source_sha256,
            expected_image_digest,
            runtime_invocation_sha256(spec),
            projection.runtime_version_sha256,
            projection.model_sha256,
            projection.tool_schema_sha256,
            projection.generation_configuration_sha256,
            projection.timeout_sha256,
            projection.resources_sha256,
            projection.retry_policy_sha256,
            projection.dataset_sha256,
            projection.profile_sha256,
            projection.evaluation_policy_sha256,
            projection.report_contract_sha256,
            projection.statistical_plan_sha256,
        )
        actual = (
            self.experiment_id,
            self.runtime_configuration_sha256,
            self.prompt_sha256,
            self.instruction_sha256,
            self.task_source_sha256,
            self.task_image_sha256,
            self.invocation_sha256,
            self.runtime_version_sha256,
            self.model_sha256,
            self.tool_schema_sha256,
            self.generation_configuration_sha256,
            self.timeout_sha256,
            self.resources_sha256,
            self.retry_policy_sha256,
            self.dataset_sha256,
            self.profile_sha256,
            self.evaluation_policy_sha256,
            self.report_contract_sha256,
            self.statistical_plan_sha256,
        )
        if actual != expected:
            raise ValueError("Runtime observation does not equal the selected Experiment authority")


def observe_runtime_authority(spec: ControlledExperimentSpecV2) -> RuntimeAuthorityObservation:
    projection = spec.core_projection()
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.runtime-authority-observation/v1",
        "experiment_id": spec.experiment_id,
        "runtime_configuration_sha256": spec.runtime.configuration_sha256,
        "prompt_sha256": spec.prompt_sha256,
        "instruction_sha256": spec.instruction_sha256,
        "task_source_sha256": spec.task.task_source.source_sha256,
        "task_image_sha256": spec.container.image.rsplit("@sha256:", 1)[1],
        "invocation_sha256": runtime_invocation_sha256(spec),
        "runtime_version_sha256": projection.runtime_version_sha256,
        "model_sha256": projection.model_sha256,
        "tool_schema_sha256": projection.tool_schema_sha256,
        "generation_configuration_sha256": projection.generation_configuration_sha256,
        "timeout_sha256": projection.timeout_sha256,
        "resources_sha256": projection.resources_sha256,
        "retry_policy_sha256": projection.retry_policy_sha256,
        "dataset_sha256": projection.dataset_sha256,
        "profile_sha256": projection.profile_sha256,
        "evaluation_policy_sha256": projection.evaluation_policy_sha256,
        "report_contract_sha256": projection.report_contract_sha256,
        "statistical_plan_sha256": projection.statistical_plan_sha256,
    }
    payload["observation_id"] = canonical_content_id(payload, excluded=frozenset())
    return RuntimeAuthorityObservation.model_validate(payload)


@dataclass(frozen=True)
class SubprocessResult:
    status: Literal["exited", "timed_out", "start_failure", "output_limit"]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    started_monotonic: float
    finished_monotonic: float
    receipt_sha256: str


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    try:
        os.killpg(process.pid, 0)
    except (ProcessLookupError, PermissionError):
        # Darwin may report EPERM for a fully killed orphaned group. Because the
        # initial SIGKILL was accepted for our same-session group, EPERM proves
        # no remaining member is signalable by the spawning identity.
        return
    raise RuntimeError("controlled subprocess process group survived cleanup")


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
    capture_root = Path(tempfile.mkdtemp(prefix="cernora-capture-", dir=cwd))
    try:
        stdout_path = capture_root / "stdout.bin"
        stderr_path = capture_root / "stderr.bin"
        with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=dict(environment),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    start_new_session=True,
                )
            except OSError as exc:
                finished = clock()
                receipt = canonical_content_id(
                    {"error": type(exc).__name__, "status": "start_failure"},
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
            status: Literal["exited", "timed_out", "start_failure", "output_limit"] = "exited"
            while process.poll() is None:
                if stdout_path.stat().st_size > MAX_CAPTURE_BYTES or (
                    stderr_path.stat().st_size > MAX_CAPTURE_BYTES
                ):
                    status = "output_limit"
                    _kill_process_group(process)
                    break
                remaining = min(started + available, deadline_monotonic) - clock()
                if remaining <= 0:
                    status = "timed_out"
                    _kill_process_group(process)
                    break
                try:
                    process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    continue
        finished = clock()
        if stdout_path.stat().st_size > MAX_CAPTURE_BYTES or (
            stderr_path.stat().st_size > MAX_CAPTURE_BYTES
        ):
            status = "output_limit"
            stdout = b""
            stderr = b""
        else:
            stdout = read_regular_file_bytes(stdout_path, maximum=MAX_CAPTURE_BYTES)
            stderr = read_regular_file_bytes(stderr_path, maximum=MAX_CAPTURE_BYTES)
        exit_code = process.returncode if status == "exited" else None
    finally:
        for child in capture_root.iterdir():
            child.unlink(missing_ok=True)
        capture_root.rmdir()
    receipt = canonical_content_id(
        {
            "command_identity_sha256": sha256_bytes(canonical_json_bytes(list(command))),
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
    "runtime_invocation_sha256",
]
