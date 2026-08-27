"""Exact Runtime observation and hard-deadline subprocess execution."""

from __future__ import annotations

import os
import signal
import stat
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
    sha256_bytes,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    ControlledExperimentSpecV2,
    Digest,
    StrictV2Contract,
)

Clock = Callable[[], float]
DiskProbe = Callable[[], int]
MAX_CAPTURE_BYTES = 1_048_576


def _path_is_inside_git_worktree(path: Path) -> bool:
    resolved = path.resolve(strict=True)
    for parent in (resolved, *resolved.parents):
        marker = parent / ".git"
        try:
            metadata = marker.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode):
            return True
    return False


def _private_capture_parent() -> Path:
    try:
        parent = Path(tempfile.gettempdir()).resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("private subprocess capture parent is unavailable") from exc
    if not parent.is_dir() or _path_is_inside_git_worktree(parent):
        raise RuntimeError("subprocess capture must remain outside every Git worktree")
    return parent


def _capture_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink)


def _read_bound_capture(
    directory_fd: int,
    name: str,
    expected: tuple[int, int, int, int],
) -> tuple[bytes, bool]:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        before = os.fstat(descriptor)
        if (
            _capture_identity(before) != expected
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
        ):
            raise RuntimeError("subprocess capture file identity changed")
        before_snapshot = (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 64 * 1024):
            total += len(chunk)
            if total > MAX_CAPTURE_BYTES:
                return b"", True
            chunks.append(chunk)
        after = os.fstat(descriptor)
        after_snapshot = (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if _capture_identity(after) != expected or after_snapshot != before_snapshot:
            raise RuntimeError("subprocess capture file changed during stable read")
        return b"".join(chunks), False
    finally:
        os.close(descriptor)


def _cleanup_bound_capture(
    capture_root: Path,
    directory_fd: int,
    directory_identity: tuple[int, int, int, int],
    file_identities: Mapping[str, tuple[int, int, int, int]],
) -> None:
    """Remove only the exact private capture entries created by this invocation."""

    try:
        current_root = capture_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError("subprocess capture directory ownership became ambiguous") from exc
    if (
        _capture_identity(os.fstat(directory_fd)) != directory_identity
        or _capture_identity(current_root) != directory_identity
        or not stat.S_ISDIR(current_root.st_mode)
        or set(os.listdir(directory_fd)) != set(file_identities)
    ):
        raise RuntimeError("subprocess capture directory ownership became ambiguous")
    for name, expected in file_identities.items():
        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError("subprocess capture file ownership became ambiguous") from exc
        if (
            _capture_identity(current) != expected
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
        ):
            raise RuntimeError("subprocess capture file ownership became ambiguous")
    for name in sorted(file_identities):
        os.unlink(name, dir_fd=directory_fd)
    capture_root.rmdir()


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
                "task_authority_sha256": spec.task.authority_sha256,
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
            spec.task.authority_sha256,
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
        "task_source_sha256": spec.task.authority_sha256,
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
    status: Literal["exited", "timed_out", "start_failure", "output_limit", "safe_stopped"]
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
    disk_free: DiskProbe | None = None,
    safe_stop_free_bytes: int | None = None,
) -> SubprocessResult:
    """Run one process group and terminate it at the earlier frozen deadline."""

    if not command:
        raise ValueError("controlled subprocess command must be non-empty")
    started = clock()
    available = min(float(timeout_seconds), deadline_monotonic - started)
    if available <= 0:
        raise TimeoutError("controlled subprocess deadline elapsed before start")
    capture_parent = _private_capture_parent()
    capture_root = Path(tempfile.mkdtemp(prefix="cernora-capture-", dir=capture_parent))
    directory_fd = os.open(
        capture_root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    directory_identity = _capture_identity(os.fstat(directory_fd))
    file_identities: dict[str, tuple[int, int, int, int]] = {}
    try:
        if (
            capture_root.resolve(strict=True).parent != capture_parent
            or _path_is_inside_git_worktree(capture_root)
            or (_capture_identity(capture_root.stat(follow_symlinks=False)) != directory_identity)
        ):
            raise RuntimeError("subprocess capture must remain outside every Git worktree")
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        stdout_fd: int | None = None
        stderr_fd: int | None = None
        try:
            stdout_fd = os.open("stdout.bin", file_flags, 0o600, dir_fd=directory_fd)
            file_identities["stdout.bin"] = _capture_identity(os.fstat(stdout_fd))
            stderr_fd = os.open("stderr.bin", file_flags, 0o600, dir_fd=directory_fd)
            file_identities["stderr.bin"] = _capture_identity(os.fstat(stderr_fd))
            directory_identity = _capture_identity(os.fstat(directory_fd))
        except BaseException:
            if stdout_fd is not None:
                os.close(stdout_fd)
            if stderr_fd is not None:
                os.close(stderr_fd)
            directory_identity = _capture_identity(os.fstat(directory_fd))
            raise
        assert stdout_fd is not None and stderr_fd is not None
        with (
            os.fdopen(stdout_fd, "wb") as stdout_handle,
            os.fdopen(stderr_fd, "wb") as stderr_handle,
        ):
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
            status: Literal[
                "exited", "timed_out", "start_failure", "output_limit", "safe_stopped"
            ] = "exited"
            while process.poll() is None:
                if (
                    disk_free is not None
                    and safe_stop_free_bytes is not None
                    and disk_free() < safe_stop_free_bytes
                ):
                    status = "safe_stopped"
                    _kill_process_group(process)
                    break
                if os.fstat(stdout_handle.fileno()).st_size > MAX_CAPTURE_BYTES or (
                    os.fstat(stderr_handle.fileno()).st_size > MAX_CAPTURE_BYTES
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
        stdout_size = os.stat("stdout.bin", dir_fd=directory_fd, follow_symlinks=False).st_size
        stderr_size = os.stat("stderr.bin", dir_fd=directory_fd, follow_symlinks=False).st_size
        if stdout_size > MAX_CAPTURE_BYTES or stderr_size > MAX_CAPTURE_BYTES:
            status = "output_limit"
            stdout = b""
            stderr = b""
        else:
            stdout, stdout_overflow = _read_bound_capture(
                directory_fd, "stdout.bin", file_identities["stdout.bin"]
            )
            stderr, stderr_overflow = _read_bound_capture(
                directory_fd, "stderr.bin", file_identities["stderr.bin"]
            )
            if stdout_overflow or stderr_overflow:
                status = "output_limit"
                stdout = b""
                stderr = b""
        exit_code = process.returncode if status == "exited" else None
    finally:
        try:
            _cleanup_bound_capture(
                capture_root,
                directory_fd,
                directory_identity,
                file_identities,
            )
        finally:
            os.close(directory_fd)
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
