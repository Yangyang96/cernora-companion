"""Production Harbor/pi executor for authority-bound controlled repairs."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any, Never, Protocol, cast
from uuid import UUID

from cernora import BatchAttemptResources, BatchLifecycleRecord

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_evaluation import (
    RepairResultRecord,
    materialize_repair_result,
)
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptRequest,
    materialize_controlled_attempt,
)
from cernora_reference_workflow.controlled_experiment_spec import ControlledExperimentSpecV2
from cernora_reference_workflow.controlled_profile import evaluate_repair_result_package
from cernora_reference_workflow.controlled_runner import (
    SAFE_STOP_FREE_BYTES,
    ControlledActiveSafeStop,
)
from cernora_reference_workflow.controlled_runtime import (
    RuntimeAuthorityObservation,
    SubprocessResult,
    run_subprocess_until,
    runtime_invocation_sha256,
)
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.runtime_policy import (
    PI_RUNTIME_ENVIRONMENT,
    PI_VERSION,
    RUNTIME_CLEANUP_RECEIPT,
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
    resolve_provider_proxy_environment,
)

AGENT_IMPORT = "cernora_reference_workflow.runtime_agent:TelemetryDisabledPi"
_AUTH_MAX_BYTES = 4 * 1024 * 1024
_TRANSIENT_PROVIDER_EXCEPTIONS = frozenset({"NonZeroAgentExitCodeError"})
_EXPECTED_RETRY_EXCEPTIONS = frozenset(
    {
        "AgentTimeoutError",
        "ApiUsageLimitError",
        "RewardFileEmptyError",
        "RewardFileNotFoundError",
        "VerifierOutputParseError",
        "VerifierTimeoutError",
    }
)
_INFRASTRUCTURE_START_EXCEPTIONS = frozenset(
    {
        "DockerComposeError",
        "EnvironmentBuildError",
        "EnvironmentStartError",
        "TaskNotFoundError",
    }
)
_TRANSIENT_MARKERS = ("gateway", "provider", "rate limit", "service unavailable", "upstream")
_TRANSIENT_STATUS_PATTERN = re.compile(r"(?<!\d)(?:408|429|500|502|503|504)(?!\d)")
_HARBOR_RESULT_FIELDS = frozenset(
    {
        "agent_execution",
        "agent_info",
        "agent_result",
        "agent_setup",
        "config",
        "environment_setup",
        "exception_info",
        "finished_at",
        "id",
        "source",
        "started_at",
        "step_results",
        "task_checksum",
        "task_id",
        "task_name",
        "trial_name",
        "trial_uri",
        "verifier",
        "verifier_result",
    }
)
_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "HOME",
        "LANG",
        "LC_ALL",
        "NO_COLOR",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
)

EnvironmentProvider = Callable[[], Mapping[str, str]]
CliValidator = Callable[[Path], None]
ImageVerifier = Callable[[ControlledExperimentSpecV2, ControlledTaskAuthority], str]
DiskProbe = Callable[[Path], int]

_REQUIRED_HARBOR_OPTIONS = (
    "--agent",
    "--agent-kwarg",
    "--delete",
    "--env",
    "--job-name",
    "--jobs-dir",
    "--max-retries",
    "--model",
    "--n-attempts",
    "--n-concurrent",
    "--override-cpus",
    "--override-memory-mb",
    "--path",
    "--yes",
)


class LiveAttemptError(ContractError):
    """The live Runtime could not produce one closed controlled Attempt."""

    def __init__(self, message: str, *, diagnostic_code: str | None = None) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


class _ProcessRunner(Protocol):
    def __call__(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline_monotonic: float,
        timeout_seconds: int,
        disk_free: Callable[[], int] | None = None,
        safe_stop_free_bytes: int | None = None,
    ) -> SubprocessResult: ...


@dataclass(frozen=True)
class ContainerRecord:
    container_id: str
    image_id: str
    created_unix_seconds: float
    labels: Mapping[str, str]


@dataclass(frozen=True)
class ContainerSnapshot:
    captured_unix_seconds: float
    records: Mapping[str, ContainerRecord]


class ContainerController(Protocol):
    def snapshot(self) -> ContainerSnapshot: ...

    def cleanup_new(
        self,
        before: ContainerSnapshot,
        *,
        expected_image_id: str,
        job_name: str,
        trial_name: str | None,
    ) -> tuple[str, ...]: ...


def attributable_container_ids(
    before: ContainerSnapshot,
    after: ContainerSnapshot,
    *,
    expected_image_id: str,
    job_name: str,
    trial_name: str | None,
) -> tuple[str, ...]:
    """Select only newly-created, exact-image, exact-label Goal containers."""

    target_values = [job_name]
    if trial_name is not None:
        target_values.extend((trial_name, f"{trial_name}__env", f"{trial_name}__agent"))
    targets = tuple(value.lower().replace("_", "-") for value in target_values)
    selected: list[str] = []
    for identifier in sorted(set(after.records) - set(before.records)):
        record = after.records[identifier]
        label_values = {value.lower().replace("_", "-") for value in record.labels.values()}
        if (
            record.image_id == f"sha256:{expected_image_id}"
            and record.created_unix_seconds >= before.captured_unix_seconds - 1.0
            and targets
            and any(target in label_values for target in targets)
        ):
            selected.append(identifier)
    return tuple(selected)


def validate_installed_harbor_cli(executable: Path) -> None:
    python = executable.with_name("python")
    environment = {**os.environ, "COLUMNS": "240", "NO_COLOR": "1"}
    try:
        version = subprocess.run(
            (
                str(python),
                "-I",
                "-c",
                "from importlib.metadata import version; print(version('harbor'))",
            ),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        help_result = subprocess.run(
            (str(executable), "run", "--help"),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except OSError as exc:
        raise LiveAttemptError(
            "installed Harbor CLI does not match the qualified 0.16.1 surface"
        ) from exc
    if (
        version.returncode != 0
        or version.stdout.strip() != "0.16.1"
        or help_result.returncode != 0
        or any(option not in help_result.stdout for option in _REQUIRED_HARBOR_OPTIONS)
    ):
        raise LiveAttemptError("installed Harbor CLI does not match the qualified 0.16.1 surface")


def _object(path: Path, *, label: str) -> dict[str, Any]:
    payload = load_json_bytes(read_regular_file_bytes(path))
    if not isinstance(payload, dict):
        raise LiveAttemptError(f"{label} must contain one JSON object")
    return cast(dict[str, Any], payload)


def _path_is_inside_git_worktree(path: Path) -> bool:
    resolved = path.resolve()
    start = resolved if resolved.is_dir() else resolved.parent
    for parent in (start, *start.parents):
        marker = parent / ".git"
        try:
            metadata = marker.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode):
            return True
    return False


def _stable_auth_markers(auth_path: Path, repository_root: Path) -> tuple[bytes, ...]:
    del repository_root
    if not auth_path.is_absolute() or auth_path.name in {"", ".", ".."}:
        raise LiveAttemptError("auth file must be an explicit absolute regular file")
    if _path_is_inside_git_worktree(auth_path):
        raise LiveAttemptError("auth file must remain outside every Git worktree")
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(auth_path.parent, parent_flags)
    except OSError as exc:
        raise LiveAttemptError("auth parent must be one no-follow directory") from exc
    try:
        parent_before = os.fstat(parent_fd)
        path_before = os.stat(auth_path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(path_before.st_mode) or path_before.st_nlink != 1:
            raise LiveAttemptError("auth file must be one no-follow regular file")
        file_fd = os.open(auth_path.name, file_flags, dir_fd=parent_fd)
        try:
            file_before = os.fstat(file_fd)
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(file_fd, 64 * 1024):
                total += len(chunk)
                if total > _AUTH_MAX_BYTES:
                    raise LiveAttemptError("auth file exceeds the private read limit")
                chunks.append(chunk)
            file_after = os.fstat(file_fd)
        finally:
            os.close(file_fd)
        path_after = os.stat(auth_path.name, dir_fd=parent_fd, follow_symlinks=False)
        parent_after = os.fstat(parent_fd)
    except OSError as exc:
        raise LiveAttemptError("auth file changed during stable no-follow read") from exc
    finally:
        os.close(parent_fd)

    def metadata(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    if (
        metadata(parent_before) != metadata(parent_after)
        or metadata(path_before) != metadata(path_after)
        or metadata(file_before) != metadata(file_after)
        or (file_before.st_dev, file_before.st_ino) != (path_before.st_dev, path_before.st_ino)
    ):
        raise LiveAttemptError("auth file changed during stable no-follow read")
    payload = load_json_bytes(b"".join(chunks))
    if not isinstance(payload, dict):
        raise LiveAttemptError("auth file must contain one JSON object")
    sensitive_names = ("account", "email", "key", "organization", "secret", "token")
    markers: set[bytes] = set()

    def collect(value: object, *, sensitive: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                collect(
                    item,
                    sensitive=sensitive
                    or any(name in str(key).lower() for name in sensitive_names),
                )
        elif isinstance(value, list):
            for item in value:
                collect(item, sensitive=sensitive)
        elif sensitive and isinstance(value, str) and len(value) >= 6:
            markers.add(value.encode("utf-8"))

    collect(payload)
    if not markers:
        raise LiveAttemptError("auth file has no privately verifiable secret markers")
    return tuple(sorted(markers))


def _child_environment(
    ambient: Mapping[str, str],
    auth_path: Path,
    proxy_environment: Mapping[str, str],
) -> dict[str, str]:
    child = {key: ambient[key] for key in _CHILD_ENV_ALLOWLIST if ambient.get(key)}
    child["PI_AUTH_JSON_PATH"] = str(auth_path)
    child.update(proxy_environment)
    return child


def _assert_private_values_absent(
    root: Path,
    *,
    auth_path: Path,
    markers: tuple[bytes, ...],
    proxy_environment: Mapping[str, str],
    explicit_proxy_endpoints: tuple[str, ...],
    process: SubprocessResult,
) -> None:
    try:
        resolved_auth_path = auth_path.resolve(strict=True)
    except OSError as exc:
        raise LiveAttemptError(
            "auth path could not be resolved for private-value scanning"
        ) from exc
    prohibited = (
        str(auth_path).encode("utf-8"),
        str(resolved_auth_path).encode("utf-8"),
        *markers,
        *(value.encode("utf-8") for name, value in proxy_environment.items() if name != "NO_PROXY"),
        *(value.encode("utf-8") for value in explicit_proxy_endpoints),
    )
    if any(marker in process.stdout or marker in process.stderr for marker in prohibited):
        raise LiveAttemptError("private value appeared in Harbor process output")
    if not root.is_dir():
        return
    for path in closed_regular_tree(root).values():
        if path.name == "auth.json":
            raise LiveAttemptError("Harbor retained a prohibited authentication artifact")
        data = read_regular_file_bytes(path, maximum=8 * 1024 * 1024)
        if any(marker in data for marker in prohibited):
            raise LiveAttemptError("private value appeared in a Harbor artifact")


class DockerContainerController:
    def snapshot(self) -> ContainerSnapshot:
        listed = subprocess.run(
            ("docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}}"),
            check=True,
            capture_output=True,
            text=True,
        )
        identifiers = tuple(item for item in listed.stdout.splitlines() if item)
        records: dict[str, ContainerRecord] = {}
        if identifiers:
            inspected = subprocess.run(
                ("docker", "inspect", *identifiers),
                check=True,
                capture_output=True,
            )
            payload = load_json_bytes(inspected.stdout)
            if not isinstance(payload, list):
                raise LiveAttemptError("Docker inspect did not return one container list")
            for item in payload:
                if not isinstance(item, dict):
                    raise LiveAttemptError("Docker container observation is malformed")
                config = item.get("Config")
                if not isinstance(config, dict) or not isinstance(config.get("Labels"), dict):
                    raise LiveAttemptError("Docker container labels are unavailable")
                created = item.get("Created")
                identifier = item.get("Id")
                image = item.get("Image")
                if not all(isinstance(value, str) for value in (created, identifier, image)):
                    raise LiveAttemptError("Docker container identity is malformed")
                assert isinstance(created, str)
                assert isinstance(identifier, str)
                assert isinstance(image, str)
                created_at = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                labels = {
                    str(key): str(value)
                    for key, value in cast(dict[object, object], config["Labels"]).items()
                }
                records[identifier] = ContainerRecord(identifier, image, created_at, labels)
        return ContainerSnapshot(time.time(), records)

    def cleanup_new(
        self,
        before: ContainerSnapshot,
        *,
        expected_image_id: str,
        job_name: str,
        trial_name: str | None,
    ) -> tuple[str, ...]:
        after = self.snapshot()
        selected = list(
            attributable_container_ids(
                before,
                after,
                expected_image_id=expected_image_id,
                job_name=job_name,
                trial_name=trial_name,
            )
        )
        for identifier in selected:
            subprocess.run(("docker", "rm", "-f", identifier), check=True, capture_output=True)
        remaining = self.snapshot().records
        if any(identifier in remaining for identifier in selected):
            raise LiveAttemptError("exact Goal container survived force removal")
        return tuple(selected)


def _verify_local_task_image(
    specification: ControlledExperimentSpecV2,
    task: ControlledTaskAuthority,
) -> str:
    expected = f"sha256:{specification.container.image.rsplit('@sha256:', 1)[1]}"
    inspected = subprocess.run(
        ("docker", "image", "inspect", expected, "--format", "{{.Id}} {{.Os}}/{{.Architecture}}"),
        check=False,
        capture_output=True,
        text=True,
    )
    if inspected.returncode != 0 or inspected.stdout.strip() != (
        f"{expected} {specification.container.platform}"
    ):
        raise LiveAttemptError("local task image identity or platform is unavailable")
    created = subprocess.run(
        ("docker", "create", "--entrypoint", "/bin/true", expected),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not created:
        raise LiveAttemptError("task image workspace probe did not create one container")
    probe = Path(tempfile.mkdtemp(prefix="cernora-image-workspace-"))
    try:
        subprocess.run(("docker", "cp", f"{created}:/workspace/.", str(probe)), check=True)
        observed = {
            path: read_regular_file_bytes(source)
            for path, source in closed_regular_tree(probe).items()
        }
        expected_workspace = {item.path: item.content() for item in task.workspace_files}
        if observed != expected_workspace:
            raise LiveAttemptError("pinned task image workspace contradicts task authority")
    finally:
        subprocess.run(("docker", "rm", "-f", created), check=False, capture_output=True)
        removal = subprocess.run(
            ("docker", "container", "inspect", created),
            check=False,
            capture_output=True,
        )
        shutil.rmtree(probe, ignore_errors=True)
    if removal.returncode == 0:
        raise LiveAttemptError("task image workspace probe container survived cleanup")
    return expected.removeprefix("sha256:")


def _verify_task_binding(spec: ControlledExperimentSpecV2, task: ControlledTaskAuthority) -> None:
    if (
        spec.task.task_id != task.case.case_id
        or spec.task.task_version != task.case.case_version
        or spec.task.case_set != task.case.case_set
        or spec.task.content_sha256 != task.case_sha256
        or spec.task.task_source.payload != task.case.model_dump(mode="json")
        or spec.task.authority_id != task.authority_id
        or spec.task.authority_sha256 != task.authority_sha256
        or spec.task.authority_source.payload != task.model_dump(mode="json")
        or spec.task.allowed_paths != task.allowed_paths
        or spec.task.protected_paths != task.protected_paths
        or spec.test_runner.command != task.test_command
        or spec.test_runner.test_source_sha256 != task.test_source_sha256
    ):
        raise LiveAttemptError("Experiment does not bind the exact controlled task authority")


def compose_controlled_instruction(
    task: ControlledTaskAuthority,
    request: ControlledAttemptRequest,
) -> str:
    def text(source: object, label: str) -> str:
        payload = getattr(source, "payload", None)
        allowed = {"text"} if label == "Instruction" else {"selected_failure", "text"}
        if (
            not isinstance(payload, dict)
            or set(payload) not in ({"text"}, allowed)
            or not isinstance(payload["text"], str)
        ):
            raise LiveAttemptError(f"{label} authority has an invalid strict text payload")
        return payload["text"]

    spec = request.specification
    return (
        "\n\n".join(
            (
                task.case.input.prompt,
                text(spec.prompt_source, "Prompt"),
                text(spec.instruction_source, "Instruction"),
            )
        )
        + "\n"
    )


_VERIFIER_DRIVER = r"""from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs.sort(); files.sort()
        base_path = Path(base)
        for name in (*dirs, *files):
            path = base_path / name
            if path.is_symlink():
                raise RuntimeError("verifier tree contains a symlink")
        for name in files:
            path = base_path / name
            if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                raise RuntimeError("verifier tree contains a non-regular file")
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result

parser = argparse.ArgumentParser()
parser.add_argument("--candidate", type=Path, required=True)
parser.add_argument("--authority", type=Path, required=True)
parser.add_argument("--test-root", type=Path, required=True)
parser.add_argument("--logs", type=Path, required=True)
args = parser.parse_args()
authority_raw = args.authority.read_bytes()
authority = json.loads(authority_raw)
if authority_raw != json.dumps(authority, sort_keys=True, separators=(",", ":")).encode():
    raise RuntimeError("verifier authority is not canonical JSON")
if set(authority) != {"allowed_paths", "test_command", "test_files", "workspace_files"}:
    raise RuntimeError("verifier authority has an unknown or missing field")
if not isinstance(authority["test_command"], list) or not authority["test_command"] or not all(
    isinstance(value, str) and value for value in authority["test_command"]
):
    raise RuntimeError("verifier command is malformed")
if tree(args.test_root) != authority["test_files"]:
    raise RuntimeError("verifier test tree does not exactly equal authority")
for relative, expected in authority["test_files"].items():
    source = args.test_root / relative
    if digest(source.read_bytes()) != expected:
        raise RuntimeError("bound test file digest mismatch")
    destination = args.candidate / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
before = tree(args.candidate)
for relative, expected in authority["test_files"].items():
    if before.get(relative) != expected:
        raise RuntimeError("candidate test file did not bind authority")
args.logs.mkdir(parents=True, exist_ok=True)
stdout_path = args.logs / "stdout.txt"
stderr_path = args.logs / "stderr.txt"
started = time.monotonic()
with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
    completed = subprocess.run(
        authority["test_command"], cwd=args.candidate, stdout=stdout, stderr=stderr
    )
duration = max(0, int((time.monotonic() - started) * 1000))
after = tree(args.candidate)
receipt = {
    "allowed_paths": authority["allowed_paths"],
    "authority_workspace": authority["workspace_files"],
    "candidate_before": before,
    "candidate_after": after,
    "duration_milliseconds": duration,
    "exit_code": completed.returncode,
    "schema_version": "cernora.reference.controlled-verifier-execution/v1",
    "stderr_sha256": digest(stderr_path.read_bytes()),
    "stdout_sha256": digest(stdout_path.read_bytes()),
    "test_command": authority["test_command"],
    "test_files": authority["test_files"],
    "working_directory": "candidate",
}
(args.logs / "execution-receipt.json").write_bytes(
    json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
)
(args.logs / "exit-code.txt").write_text(f"{completed.returncode}\n", encoding="ascii")
(args.logs / "duration-milliseconds.txt").write_text(f"{duration}\n", encoding="ascii")
(args.logs / "reward.txt").write_text(
    "1\n" if completed.returncode == 0 else "0\n", encoding="ascii"
)
"""


def _verifier_authority(task: ControlledTaskAuthority) -> dict[str, object]:
    return {
        "allowed_paths": list(task.allowed_paths),
        "test_command": list(task.test_command),
        "test_files": {item.path: item.sha256 for item in task.test_files},
        "workspace_files": {item.path: item.sha256 for item in task.workspace_files},
    }


def _materialize_task(
    request: ControlledAttemptRequest,
    task: ControlledTaskAuthority,
    task_root: Path,
) -> None:
    spec = request.specification
    environment = task_root / "environment"
    tests = task_root / "tests"
    bound_tests = tests / "authority"
    environment.mkdir(parents=True)
    bound_tests.mkdir(parents=True)
    for item in task.test_files:
        path = bound_tests / item.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item.content())
    (tests / "verifier_driver.py").write_text(_VERIFIER_DRIVER, encoding="utf-8")
    (tests / "verifier-authority.json").write_bytes(canonical_json_bytes(_verifier_authority(task)))
    instruction = compose_controlled_instruction(task, request)
    (task_root / "instruction.md").write_text(instruction, encoding="utf-8")
    image_digest = spec.container.image.rsplit("@sha256:", 1)[1]
    task_toml = (
        'schema_version = "1.3"\n\n'
        "[metadata]\n"
        'author_name = "Cernora contributors"\n'
        'author_email = "noreply@example.invalid"\n'
        'difficulty = "hard"\n'
        'category = "software-engineering"\n'
        'tags = ["python", "repair", "deterministic"]\n\n'
        "[verifier]\n"
        f"timeout_sec = {float(spec.limits.timeout_seconds)!r}\n\n"
        "[agent]\n"
        f"timeout_sec = {float(spec.limits.timeout_seconds)!r}\n\n"
        "[environment]\n"
        f'docker_image = "sha256:{image_digest}"\n'
        'workdir = "/workspace"\n'
        'network_mode = "public"\n'
    )
    (task_root / "task.toml").write_text(task_toml, encoding="utf-8")
    test_script = (
        "#!/bin/sh\nset -eu\n"
        "mkdir -p /logs/verifier/candidate\n"
        "cp -a /workspace/. /logs/verifier/candidate/\n"
        "python /tests/verifier_driver.py "
        "--candidate /logs/verifier/candidate "
        "--authority /tests/verifier-authority.json "
        "--test-root /tests/authority --logs /logs/verifier\n"
    )
    test_sh = tests / "test.sh"
    test_sh.write_text(test_script, encoding="utf-8")
    test_sh.chmod(0o755)


def _single_value(command: tuple[str, ...], option: str) -> str:
    positions = [index for index, value in enumerate(command) if value == option]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise LiveAttemptError(f"actual Harbor argv has invalid {option}")
    return command[positions[0] + 1]


def _repeated_values(command: tuple[str, ...], option: str) -> tuple[str, ...]:
    positions = [index for index, value in enumerate(command) if value == option]
    if any(index + 1 >= len(command) for index in positions):
        raise LiveAttemptError(f"actual Harbor argv has invalid {option}")
    return tuple(command[index + 1] for index in positions)


def _validate_actual_argv(
    command: tuple[str, ...],
    request: ControlledAttemptRequest,
    *,
    task_root: Path,
    job_root: Path,
    job_name: str,
    proxy_environment: Mapping[str, str],
) -> None:
    spec = request.specification
    flags = {"--delete", "--yes"}
    if command[:2] != (str(command[0]), "run") or any(command.count(flag) != 1 for flag in flags):
        raise LiveAttemptError("actual Harbor argv omits exact delete/yes controls")
    expected = {
        "-p": str(task_root),
        "-a": AGENT_IMPORT,
        "-m": spec.runtime.model,
        "-e": "docker",
        "--agent-setup-timeout-multiplier": "4",
        "--agent-timeout-multiplier": "1",
        "--override-cpus": str(spec.limits.cpu_millis // 1000),
        "--override-memory-mb": str(spec.limits.memory_mebibytes),
        "-o": str(job_root),
        "--job-name": job_name,
        "-n": "1",
        "-k": "1",
        "-r": "0",
    }
    if any(_single_value(command, option) != value for option, value in expected.items()):
        raise LiveAttemptError("actual Harbor argv drifts from Experiment authority")
    kwargs = set(_repeated_values(command, "--ak"))
    if kwargs != {
        f"version={spec.runtime.version}",
        f"thinking={spec.runtime.reasoning_effort}",
    }:
        raise LiveAttemptError("actual Harbor agent kwargs drift from Runtime authority")
    if _repeated_values(command, "--ae"):
        raise LiveAttemptError("actual Harbor argv must not persist private proxy endpoints")
    if tuple(sorted(proxy_environment)) not in (
        (),
        ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"),
    ):
        raise LiveAttemptError("explicit proxy projection is incomplete")


def _expected_agent_config(spec: ControlledExperimentSpecV2) -> dict[str, object]:
    return {
        "name": AGENT_IMPORT,
        "import_path": None,
        "model_name": spec.runtime.model,
        "n_concurrent": None,
        "concurrency_group": None,
        "skills": [],
        "override_timeout_sec": None,
        "override_setup_timeout_sec": None,
        "max_timeout_sec": None,
        "extra_allowed_hosts": [],
        "include_logs": [],
        "exclude_logs": [],
        "kwargs": {
            "thinking": spec.runtime.reasoning_effort,
            "version": spec.runtime.version,
        },
        "env": {},
        "mcp_servers": [],
    }


def _expected_environment_config(spec: ControlledExperimentSpecV2) -> dict[str, object]:
    return {
        "type": "docker",
        "import_path": None,
        "force_build": False,
        "delete": True,
        "cpu_enforcement_policy": "auto",
        "memory_enforcement_policy": "auto",
        "override_cpus": spec.limits.cpu_millis // 1000,
        "override_memory_mb": spec.limits.memory_mebibytes,
        "override_storage_mb": None,
        "override_gpus": None,
        "override_tpu": None,
        "mounts": None,
        "extra_docker_compose": [],
        "env": {},
        "kwargs": {},
        "extra_allowed_hosts": [],
    }


def _expected_verifier_config() -> dict[str, object]:
    return {
        "disable": False,
        "env": {},
        "exclude_logs": [],
        "import_path": None,
        "include_logs": [],
        "kwargs": {},
        "max_timeout_sec": None,
        "override_timeout_sec": None,
    }


def _json_type_strict_equal(actual: object, expected: object) -> bool:
    """Compare parsed JSON without Python's bool/int/float equality coercions."""

    if type(actual) is dict and type(expected) is dict:
        actual_mapping = cast(dict[object, object], actual)
        expected_mapping = cast(dict[object, object], expected)
        if not all(type(key) is str for key in (*actual_mapping, *expected_mapping)):
            return False
        if set(actual_mapping) != set(expected_mapping):
            return False
        return all(
            _json_type_strict_equal(actual_mapping[key], expected_mapping[key])
            for key in expected_mapping
        )
    if type(actual) is list and type(expected) is list:
        actual_array = cast(list[object], actual)
        expected_array = cast(list[object], expected)
        return len(actual_array) == len(expected_array) and all(
            _json_type_strict_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual_array, expected_array, strict=True)
        )
    if type(actual) is not type(expected):
        return False
    if actual is None or type(actual) in {bool, int, str}:
        return actual == expected
    if type(actual) is float:
        return isfinite(actual) and actual == expected
    return False


def _valid_pi_timeout_agent_result(value: object) -> bool:
    """Validate the two AgentContext shapes pi can close after cancellation."""

    if not isinstance(value, dict) or set(value) != {
        "cost_usd",
        "metadata",
        "n_cache_tokens",
        "n_input_tokens",
        "n_output_tokens",
        "rollout_details",
    }:
        return False
    counts = tuple(
        value[field] for field in ("n_cache_tokens", "n_input_tokens", "n_output_tokens")
    )
    counts_are_empty = all(count is None for count in counts)
    counts_are_populated = all(type(count) is int and count >= 0 for count in counts)
    cost = value["cost_usd"]
    valid_cost = cost is None or (type(cost) is float and isfinite(cost) and cost >= 0.0)
    return (
        ((counts_are_empty and cost is None) or (counts_are_populated and valid_cost))
        and value["metadata"] is None
        and value["rollout_details"] is None
    )


def _validate_job_config(
    config: Mapping[str, object],
    request: ControlledAttemptRequest,
    *,
    task_root: Path,
    job_root: Path,
    job_name: str,
    proxy_environment: Mapping[str, str],
) -> None:
    spec = request.specification
    if type(config) is not dict:
        raise LiveAttemptError("resolved Harbor job config is not one strict JSON object")
    retry = config.get("retry")
    if type(retry) is not dict:
        raise LiveAttemptError("resolved Harbor retry config is not one strict JSON object")
    retry_mapping = cast(dict[str, object], retry)
    exclusions = retry_mapping.get("exclude_exceptions")
    if (
        type(exclusions) is not list
        or len(exclusions) != len(_EXPECTED_RETRY_EXCEPTIONS)
        or any(type(item) is not str for item in exclusions)
        or len(set(exclusions)) != len(exclusions)
        or frozenset(exclusions) != _EXPECTED_RETRY_EXCEPTIONS
    ):
        raise LiveAttemptError("resolved Harbor retry exclusions drift from argv authority")
    expected = {
        "job_name": job_name,
        "jobs_dir": str(job_root),
        "n_attempts": 1,
        "install_only": False,
        "timeout_multiplier": 1.0,
        "agent_timeout_multiplier": 1.0,
        "verifier_timeout_multiplier": None,
        "agent_setup_timeout_multiplier": 4.0,
        "environment_build_timeout_multiplier": None,
        "debug": False,
        "n_concurrent_trials": 1,
        "quiet": False,
        "retry": {
            "max_retries": 0,
            "min_wait_sec": 1.0,
            "max_wait_sec": 60.0,
            "wait_multiplier": 1.0,
            "include_exceptions": None,
            "exclude_exceptions": sorted(_EXPECTED_RETRY_EXCEPTIONS),
        },
        "environment": _expected_environment_config(spec),
        "verifier": _expected_verifier_config(),
        "metrics": [],
        "agents": [_expected_agent_config(spec)],
        "datasets": [],
        "tasks": [
            {
                "path": str(task_root),
                "git_url": None,
                "git_commit_id": None,
                "name": None,
                "ref": None,
                "overwrite": False,
                "download_dir": None,
                "source": None,
            }
        ],
        "artifacts": [],
        "extra_instruction_paths": [],
    }
    normalized = dict(config)
    normalized_retry = dict(retry_mapping)
    normalized_retry["exclude_exceptions"] = sorted(_EXPECTED_RETRY_EXCEPTIONS)
    normalized["retry"] = normalized_retry
    if not _json_type_strict_equal(normalized, expected):
        raise LiveAttemptError("resolved Harbor job config drifts from actual argv authority")


def _validate_trial_result(
    result: Mapping[str, object],
    request: ControlledAttemptRequest,
    task: ControlledTaskAuthority,
    *,
    task_root: Path,
    job_root: Path,
    job_name: str,
    task_checksum: str,
) -> None:
    spec = request.specification
    agent_info = result.get("agent_info")
    config = result.get("config")
    if set(result) != _HARBOR_RESULT_FIELDS:
        raise LiveAttemptError("Harbor result does not match the exact 0.16.1 structure")
    try:
        UUID(cast(str, result.get("id")))
    except (TypeError, ValueError, AttributeError) as exc:
        raise LiveAttemptError("Harbor result identity is malformed") from exc
    task_id = result.get("task_id")
    trial_uri = result.get("trial_uri")
    trial_name = result.get("trial_name")
    if (
        task_id != {"path": str(task_root)}
        or not isinstance(trial_uri, str)
        or not trial_uri
        or not isinstance(trial_name, str)
        or not trial_name
    ):
        raise LiveAttemptError("Harbor result task identity is malformed")
    if not isinstance(agent_info, dict) or not isinstance(config, dict):
        raise LiveAttemptError("Harbor result omits agent/config observations")
    model = agent_info.get("model_info")
    trial_agent = config.get("agent")
    trial_environment = config.get("environment")
    trial_task = config.get("task")
    if not all(
        isinstance(item, dict) for item in (model, trial_agent, trial_environment, trial_task)
    ):
        raise LiveAttemptError("Harbor result has malformed runtime observations")
    assert isinstance(model, dict)
    assert isinstance(trial_agent, dict)
    assert isinstance(trial_environment, dict)
    assert isinstance(trial_task, dict)
    job_id = config.get("job_id")
    try:
        UUID(cast(str, job_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise LiveAttemptError("Harbor result job identity is malformed") from exc
    expected_config: dict[str, object] = {
        "task": {
            "path": str(task_root),
            "git_url": None,
            "git_commit_id": None,
            "name": None,
            "ref": None,
            "overwrite": False,
            "download_dir": None,
            "source": None,
        },
        "trial_name": trial_name,
        "trials_dir": str(job_root / job_name),
        "install_only": False,
        "timeout_multiplier": 1.0,
        "agent_timeout_multiplier": 1.0,
        "verifier_timeout_multiplier": None,
        "agent_setup_timeout_multiplier": 4.0,
        "environment_build_timeout_multiplier": None,
        "agent": _expected_agent_config(spec),
        "environment": _expected_environment_config(spec),
        "verifier": _expected_verifier_config(),
        "artifacts": [],
        "extra_instruction_paths": [],
        "job_id": job_id,
    }
    if not _json_type_strict_equal(config, expected_config):
        raise LiveAttemptError(
            "actual Harbor Trial config drifts from Runtime/task authority",
            diagnostic_code="trial-config-authority-rejected",
        )
    # The pinned pi Runtime is configured with a provider/model pair; Harbor 0.16.1
    # reports the split representation in agent_info.model_info, and the slash
    # form never appears in a real pi result.
    prov, separator, model_name = spec.runtime.model.partition("/")
    if separator:
        expected_model_info: dict[str, object] = {"name": model_name, "provider": prov}
    else:
        expected_model_info = {"name": spec.runtime.model, "provider": None}
    if (
        set(agent_info) != {"name", "version", "model_info"}
        or set(model) != {"name", "provider"}
        or not _json_type_strict_equal(result.get("task_name"), task.case.case_id)
        or not _json_type_strict_equal(result.get("source"), None)
        or not _json_type_strict_equal(result.get("task_checksum"), task_checksum)
        or not _json_type_strict_equal(
            agent_info,
            {
                "name": "pi",
                "version": spec.runtime.version,
                "model_info": expected_model_info,
            },
        )
    ):
        raise LiveAttemptError("actual Harbor result drifts from Runtime/task authority")


def _runtime_observation(
    request: ControlledAttemptRequest,
    task: ControlledTaskAuthority,
    command: tuple[str, ...],
    *,
    task_root: Path,
    job_root: Path,
    job_name: str,
    proxy_environment: Mapping[str, str],
    result: Mapping[str, object],
    task_checksum: str,
) -> RuntimeAuthorityObservation:
    spec = request.specification
    _validate_actual_argv(
        command,
        request,
        task_root=task_root,
        job_root=job_root,
        job_name=job_name,
        proxy_environment=proxy_environment,
    )
    job_config = _object(job_root / job_name / "config.json", label="Harbor job config")
    _validate_job_config(
        job_config,
        request,
        task_root=task_root,
        job_root=job_root,
        job_name=job_name,
        proxy_environment=proxy_environment,
    )
    _validate_trial_result(
        result,
        request,
        task,
        task_root=task_root,
        job_root=job_root,
        job_name=job_name,
        task_checksum=task_checksum,
    )
    task_config = tomllib.loads(read_regular_file_bytes(task_root / "task.toml").decode("utf-8"))
    environment = task_config.get("environment")
    expected_image = spec.container.image.rsplit("@sha256:", 1)[1]
    if not isinstance(environment, dict) or environment.get("docker_image") != (
        f"sha256:{expected_image}"
    ):
        raise LiveAttemptError("materialized Harbor task image contradicts authority")
    projection = spec.core_projection()
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.runtime-authority-observation/v1",
        "experiment_id": spec.experiment_id,
        "runtime_configuration_sha256": spec.runtime.configuration_sha256,
        "prompt_sha256": spec.prompt_source.source_sha256,
        "instruction_sha256": spec.instruction_source.source_sha256,
        "task_source_sha256": spec.task.authority_sha256,
        "task_image_sha256": expected_image,
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
    observation = RuntimeAuthorityObservation.model_validate(payload)
    observation.verify(spec)
    return observation


def _trial_result(job_root: Path, job_name: str) -> tuple[Path, dict[str, Any]] | None:
    job = job_root / job_name
    if not job.is_dir() or job.is_symlink():
        return None
    trials = tuple(
        item
        for item in job.iterdir()
        if item.is_dir() and not item.is_symlink() and (item / "result.json").is_file()
    )
    if len(trials) > 1:
        raise LiveAttemptError("Harbor job contains more than one closed Trial")
    if not trials:
        return None
    result = _object(trials[0] / "result.json", label="Harbor result")
    if result.get("trial_name") != trials[0].name:
        raise LiveAttemptError("Harbor result trial identity contradicts its closed directory")
    if result.get("trial_uri") != trials[0].resolve().as_uri():
        raise LiveAttemptError("Harbor result URI contradicts its closed directory")
    return trials[0], result


def _trial_name_hint(job_root: Path, job_name: str) -> str | None:
    job = job_root / job_name
    if not job.is_dir() or job.is_symlink():
        return None
    trials = tuple(item for item in job.iterdir() if item.is_dir() and not item.is_symlink())
    if len(trials) > 1:
        raise LiveAttemptError("Harbor job contains more than one Trial directory")
    return trials[0].name if trials else None


def _classify_preterminal(
    process: SubprocessResult,
    result: Mapping[str, object] | None,
    request: ControlledAttemptRequest,
    task: ControlledTaskAuthority,
    *,
    task_root: Path,
    job_root: Path,
    job_name: str,
    task_checksum: str,
    attempt_envelope_timeout_seconds: int,
) -> tuple[str, bool, str] | None:
    if process.status in {"timed_out", "output_limit"}:
        return "runtime_pre_terminal_failure", False, "process-envelope-failure"
    if result is None:
        return (
            None
            if process.exit_code == 0
            else ("runtime_pre_terminal_failure", False, "missing-trial-result")
        )
    _validate_trial_result(
        result,
        request,
        task,
        task_root=task_root,
        job_root=job_root,
        job_name=job_name,
        task_checksum=task_checksum,
    )
    exception = result.get("exception_info")
    agent_result = result.get("agent_result")
    verifier_result = result.get("verifier_result")
    if process.exit_code == 0 and exception is None:
        return None
    if not isinstance(exception, dict) or set(exception) != {
        "exception_type",
        "exception_message",
        "exception_traceback",
        "occurred_at",
    }:
        raise LiveAttemptError("pre-terminal Harbor result has malformed exception evidence")
    exception_type = exception.get("exception_type")
    message = exception.get("exception_message")
    traceback_value = exception.get("exception_traceback")
    occurred_at = exception.get("occurred_at")
    if not all(
        isinstance(value, str) and value
        for value in (exception_type, message, traceback_value, occurred_at)
    ):
        raise LiveAttemptError("pre-terminal Harbor result has incomplete exception evidence")
    assert isinstance(occurred_at, str)
    try:
        exception_at = datetime.fromisoformat(occurred_at)
    except ValueError as exc:
        raise LiveAttemptError("pre-terminal Harbor result has invalid exception timing") from exc
    agent_execution = result.get("agent_execution")
    if agent_execution is not None and (
        not isinstance(agent_execution, dict)
        or set(agent_execution) != {"started_at", "finished_at"}
        or not all(value is None or isinstance(value, str) for value in agent_execution.values())
    ):
        raise LiveAttemptError("pre-terminal Harbor result has malformed execution timing")
    if isinstance(agent_execution, dict):
        try:
            for value in agent_execution.values():
                if isinstance(value, str):
                    datetime.fromisoformat(value)
        except ValueError as exc:
            raise LiveAttemptError(
                "pre-terminal Harbor result has invalid execution timing"
            ) from exc
    assert isinstance(exception_type, str)
    assert isinstance(message, str)
    assert isinstance(traceback_value, str)
    if exception_type == "AgentTimeoutError":
        rewards = verifier_result.get("rewards") if isinstance(verifier_result, dict) else None
        verifier_timing = result.get("verifier")

        def reject_timeout(code: str) -> Never:
            raise LiveAttemptError(
                "Agent timeout result contradicts Harbor phase evidence",
                diagnostic_code=code,
            )

        if not _valid_pi_timeout_agent_result(agent_result):
            reject_timeout("agent-timeout-agent-result")
        if not isinstance(agent_execution, dict) or not all(
            isinstance(agent_execution[field], str) and agent_execution[field]
            for field in ("started_at", "finished_at")
        ):
            reject_timeout("agent-timeout-agent-timing-shape")
        # Harbor records the AgentTimeoutError before it attempts verification. A
        # post-timeout verifier failure therefore leaves verifier_result unset while
        # preserving complete timeout evidence. That missing evaluation evidence can
        # never produce a RepairResult, but it must not erase the terminal timeout.
        if verifier_result is not None and (
            not isinstance(verifier_result, dict)
            or set(verifier_result) != {"rewards"}
            or not isinstance(rewards, dict)
            or set(rewards) != {"reward"}
            or type(rewards["reward"]) is not float
            or rewards["reward"] not in {0.0, 1.0}
        ):
            reject_timeout("agent-timeout-verifier-result")
        if (
            not isinstance(verifier_timing, dict)
            or set(verifier_timing) != {"started_at", "finished_at"}
            or not all(
                isinstance(verifier_timing[field], str) and verifier_timing[field]
                for field in ("started_at", "finished_at")
            )
        ):
            reject_timeout("agent-timeout-verifier-timing-shape")
        if message != (
            "Agent execution timed out after "
            f"{float(request.specification.limits.timeout_seconds)} seconds"
        ):
            reject_timeout("agent-timeout-message")
        if not traceback_value.rstrip().endswith(f"AgentTimeoutError: {message}"):
            reject_timeout("agent-timeout-traceback")
        assert isinstance(agent_execution["started_at"], str)
        assert isinstance(agent_execution["finished_at"], str)
        assert isinstance(verifier_timing["started_at"], str)
        assert isinstance(verifier_timing["finished_at"], str)
        try:
            agent_started = datetime.fromisoformat(agent_execution["started_at"])
            agent_finished = datetime.fromisoformat(agent_execution["finished_at"])
            verifier_started = datetime.fromisoformat(verifier_timing["started_at"])
            verifier_finished = datetime.fromisoformat(verifier_timing["finished_at"])
        except ValueError as exc:
            raise LiveAttemptError(
                "Agent timeout result contradicts Harbor phase evidence",
                diagnostic_code="agent-timeout-timestamp-parse",
            ) from exc
        try:
            exception_at_local = exception_at.astimezone()
        except (ValueError, OverflowError, OSError) as exc:
            raise LiveAttemptError(
                "Agent timeout result contradicts Harbor phase evidence",
                diagnostic_code="agent-timeout-exception-timezone",
            ) from exc
        if (
            agent_started.utcoffset() is None
            or agent_finished.utcoffset() is None
            or exception_at.utcoffset() is not None
            or verifier_started.utcoffset() is None
            or verifier_finished.utcoffset() is None
            or exception_at_local < agent_finished
            or exception_at_local > verifier_started
            or verifier_finished < verifier_started
            or verifier_started < agent_finished
        ):
            reject_timeout("agent-timeout-timezone-order")
        agent_elapsed = (agent_finished - agent_started).total_seconds()
        phase_elapsed = (verifier_finished - agent_started).total_seconds()
        process_elapsed = process.finished_monotonic - process.started_monotonic
        if (
            agent_elapsed < request.specification.limits.timeout_seconds
            or agent_elapsed > attempt_envelope_timeout_seconds
            or phase_elapsed > attempt_envelope_timeout_seconds
            or process_elapsed < phase_elapsed
            or process_elapsed > attempt_envelope_timeout_seconds
        ):
            reject_timeout("agent-timeout-duration-bound")
        return "timed_out", False, "agent-timeout-evidence-accepted"
    if agent_result is not None or verifier_result is not None:
        return (
            "runtime_pre_terminal_failure",
            False,
            "non-timeout-exception-with-phase-evidence",
        )
    if agent_execution is None and exception_type in _INFRASTRUCTURE_START_EXCEPTIONS:
        return "infrastructure_start_failure", True, "infrastructure-start-exception"
    normalized = message.lower()
    transient = (
        exception_type in _TRANSIENT_PROVIDER_EXCEPTIONS
        and _TRANSIENT_STATUS_PATTERN.search(normalized) is not None
        and any(marker in normalized for marker in _TRANSIENT_MARKERS)
    )
    return (
        ("transient_provider_pre_terminal", True, "transient-provider-exception")
        if transient
        else ("runtime_pre_terminal_failure", False, "unclassified-runtime-exception")
    )


def _lifecycle_attempt(
    request: ControlledAttemptRequest,
    process: SubprocessResult,
    *,
    category: str,
    retry_eligible: bool,
) -> ControlledAttempt:
    lifecycle = BatchLifecycleRecord(
        schema_version="agent.evaluator.batch-lifecycle/v1",
        category=category,  # type: ignore[arg-type]
        retry_eligible=retry_eligible,
        source_state=category.replace("_", "-"),
        receipt_sha256=process.receipt_sha256,
    )
    source_attempt_id = canonical_content_id(
        {
            "ordinal": request.ordinal,
            "process_receipt_sha256": process.receipt_sha256,
            "trial_id": request.trial_id,
        },
        excluded=frozenset(),
    )
    return materialize_controlled_attempt(
        {
            "schema_version": "cernora.reference.controlled-attempt/v1",
            "trial_id": request.trial_id,
            "ordinal": request.ordinal,
            "predecessor_attempt_id": request.predecessor_attempt_id,
            "source_attempt_id": source_attempt_id,
            "source_manifest_sha256": process.receipt_sha256,
            "retry_eligible": retry_eligible,
            "resources": BatchAttemptResources(
                duration_milliseconds=max(
                    0, int((process.finished_monotonic - process.started_monotonic) * 1000)
                )
            ).model_dump(mode="json"),
            "runtime_observation": None,
            "repair_result": None,
            "evaluation": None,
            "lifecycle": lifecycle.model_dump(mode="json"),
        }
    )


def _result_from_job(
    request: ControlledAttemptRequest,
    task: ControlledTaskAuthority,
    trial: Path,
) -> RepairResultRecord:
    verifier = trial / "verifier"
    agent = trial / "agent"
    runtime_artifacts = {
        "pi-environment.json": canonical_json_bytes(PI_RUNTIME_ENVIRONMENT),
        "runtime-policy.json": canonical_json_bytes(RUNTIME_POLICY),
        "runtime-cleanup.json": canonical_json_bytes(RUNTIME_CLEANUP_RECEIPT),
    }
    for name, expected in runtime_artifacts.items():
        if read_regular_file_bytes(agent / name) != expected:
            raise LiveAttemptError("Harbor Runtime artifact contradicts pinned authority")
    version = read_regular_file_bytes(agent / "pi-version.txt").decode("utf-8").strip()
    if version != PI_VERSION:
        raise LiveAttemptError("Harbor pi runtime version does not prove pinned policy")
    if request.specification.runtime.configuration_sha256 != RUNTIME_CONFIGURATION_SHA256:
        raise LiveAttemptError("Experiment Runtime configuration is not pinned authority")
    receipt_raw = read_regular_file_bytes(verifier / "execution-receipt.json")
    receipt = load_json_bytes(receipt_raw)
    if not isinstance(receipt, dict) or receipt_raw != canonical_json_bytes(receipt):
        raise LiveAttemptError("controlled verifier receipt is not canonical JSON")
    candidate = {
        path: sha256_bytes(read_regular_file_bytes(source))
        for path, source in closed_regular_tree(verifier / "candidate").items()
    }
    stdout = read_regular_file_bytes(verifier / "stdout.txt")
    stderr = read_regular_file_bytes(verifier / "stderr.txt")
    expected_receipt = {
        "allowed_paths": list(task.allowed_paths),
        "authority_workspace": {item.path: item.sha256 for item in task.workspace_files},
        "candidate_before": receipt.get("candidate_before"),
        "candidate_after": candidate,
        "duration_milliseconds": receipt.get("duration_milliseconds"),
        "exit_code": receipt.get("exit_code"),
        "schema_version": "cernora.reference.controlled-verifier-execution/v1",
        "stderr_sha256": sha256_bytes(stderr),
        "stdout_sha256": sha256_bytes(stdout),
        "test_command": list(task.test_command),
        "test_files": {item.path: item.sha256 for item in task.test_files},
        "working_directory": "candidate",
    }
    if receipt != expected_receipt or not isinstance(receipt.get("exit_code"), int):
        raise LiveAttemptError("controlled verifier receipt contradicts exact task authority")
    before = receipt.get("candidate_before")
    if not isinstance(before, dict) or any(
        before.get(item.path) != item.sha256 for item in task.test_files
    ):
        raise LiveAttemptError("controlled verifier did not execute all bound test files")
    initial = {
        **{item.path: item.sha256 for item in task.workspace_files},
        **{item.path: item.sha256 for item in task.test_files},
    }
    changed = tuple(
        sorted(
            path
            for path in set(initial) | set(candidate)
            if initial.get(path) != candidate.get(path)
        )
    )
    protected_before = {path: initial.get(path) for path in task.protected_paths}
    protected_after = {path: candidate.get(path) for path in task.protected_paths}
    return materialize_repair_result(
        {
            "schema_version": "cernora.reference.repair-result/v1",
            "case_id": task.case.case_id,
            "result_record_version": "agent.evaluator.result-record/v1",
            "test_authority_sha256": request.specification.test_runner.authority_sha256,
            "test_plan_sha256": request.specification.test_runner.test_plan_sha256,
            "test_source_sha256": request.specification.test_runner.test_source_sha256,
            "termination": "exited",
            "exit_code": receipt["exit_code"],
            "checks": [
                {
                    "check_id": "frozen-verifier",
                    "failure_code": task.failure_code,
                    "passed": receipt["exit_code"] == 0,
                }
            ],
            "allowed_paths": list(task.allowed_paths),
            "changed_paths": list(changed),
            "protected_paths": list(task.protected_paths),
            "protected_path_receipt": {
                "before_sha256": sha256_bytes(canonical_json_bytes(protected_before)),
                "after_sha256": sha256_bytes(canonical_json_bytes(protected_after)),
                "unchanged": protected_before == protected_after,
            },
        }
    )


class ControlledHarborAttemptExecutor:
    """One serial executor; auth/proxy exist only in the spawned child."""

    def __init__(
        self,
        *,
        repository_root: Path,
        tasks: tuple[ControlledTaskAuthority, ...],
        evaluation_root: Path,
        auth_file: Path,
        proxy_environment: Mapping[str, str],
        ambient_environment: EnvironmentProvider = lambda: os.environ,
        process_runner: _ProcessRunner = run_subprocess_until,
        container_controller: ContainerController | None = None,
        cli_validator: CliValidator = validate_installed_harbor_cli,
        image_verifier: ImageVerifier = _verify_local_task_image,
        disk_free: DiskProbe = lambda path: shutil.disk_usage(path).free,
        close_unusable_runtime_evidence: bool = False,
        attempt_envelope_grace_seconds: int = 0,
    ) -> None:
        self._repository_root = repository_root
        self._tasks = {item.case.case_id: item for item in tasks}
        self._task_suite = tasks
        self._evaluation_root = evaluation_root
        self._auth_file = auth_file
        self._proxy_environment = resolve_provider_proxy_environment(proxy_environment)
        # The pi direct-provider egress has no mandatory proxy, and the resolver returns only
        # the container projection; raw host endpoints are no longer published separately.
        self._explicit_proxy_endpoints: tuple[str, ...] = ()
        if attempt_envelope_grace_seconds < 0:
            raise ContractError("Attempt envelope grace must be non-negative")
        self._attempt_envelope_grace_seconds = attempt_envelope_grace_seconds
        self._ambient_environment = ambient_environment
        self._process_runner = process_runner
        self._containers = container_controller or DockerContainerController()
        self._image_verifier = image_verifier
        self._disk_free = disk_free
        self._close_unusable_runtime_evidence = close_unusable_runtime_evidence
        self._diagnostic_code: str | None = None
        cli_validator(repository_root / ".venv/bin/harbor")

    @property
    def enforces_hard_deadline(self) -> bool:
        return True

    @property
    def diagnostic_code(self) -> str | None:
        """Return only the fixed, value-free code for the latest closed Attempt."""

        return self._diagnostic_code

    def _command(
        self,
        request: ControlledAttemptRequest,
        task_root: Path,
        job_root: Path,
        job_name: str,
        proxy_environment: Mapping[str, str],
    ) -> tuple[str, ...]:
        spec = request.specification
        if spec.limits.cpu_millis < 1000 or spec.limits.cpu_millis % 1000:
            raise LiveAttemptError("Harbor CPU override cannot exactly represent limits")
        command = [
            str(self._repository_root / ".venv/bin/harbor"),
            "run",
            "-p",
            str(task_root),
            "-a",
            AGENT_IMPORT,
            "-m",
            spec.runtime.model,
            "-e",
            "docker",
            "--ak",
            f"version={spec.runtime.version}",
            "--ak",
            f"thinking={spec.runtime.reasoning_effort}",
            "--agent-setup-timeout-multiplier",
            "4",
            "--agent-timeout-multiplier",
            "1",
            "--override-cpus",
            str(spec.limits.cpu_millis // 1000),
            "--override-memory-mb",
            str(spec.limits.memory_mebibytes),
            "--delete",
            "-o",
            str(job_root),
            "--job-name",
            job_name,
            "-n",
            "1",
            "-k",
            "1",
            "-r",
            "0",
            "--yes",
        ]
        if tuple(sorted(proxy_environment)) not in (
            (),
            ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"),
        ):
            raise LiveAttemptError("explicit proxy projection is incomplete")
        return tuple(command)

    def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
        self._diagnostic_code = None
        task = self._tasks.get(request.slot.case_id)
        if task is None:
            raise LiveAttemptError("selected Case has no controlled task authority")
        _verify_task_binding(request.specification, task)
        temporary = Path(tempfile.mkdtemp(prefix="cernora-m4-attempt-", dir=self._evaluation_root))
        job_name = f"m4-{request.trial_id[:12]}-{request.ordinal}"
        try:
            task_root = temporary / task.case.case_id
            job_root = temporary / "job"
            task_root.mkdir()
            job_root.mkdir()
            _materialize_task(request, task, task_root)
            observed_image = self._image_verifier(request.specification, task)
            expected_image = request.specification.container.image.rsplit("@sha256:", 1)[1]
            if observed_image != expected_image:
                raise LiveAttemptError("observed local task image contradicts authority")

            # Authentication is deliberately read only after the image/task bytes pass.
            auth_markers = _stable_auth_markers(self._auth_file, self._repository_root)
            environment = _child_environment(
                self._ambient_environment(), self._auth_file, self._proxy_environment
            )
            command = self._command(request, task_root, job_root, job_name, self._proxy_environment)
            _validate_actual_argv(
                command,
                request,
                task_root=task_root,
                job_root=job_root,
                job_name=job_name,
                proxy_environment=self._proxy_environment,
            )
            try:
                from dirhash import dirhash  # type: ignore[import-untyped]

                task_checksum = cast(str, dirhash(task_root, "sha256"))
            except (ImportError, OSError, ValueError) as exc:
                raise LiveAttemptError("cannot compute the real Harbor task checksum") from exc
            before = self._containers.snapshot()
            trial_name: str | None = None
            cleanup_job = job_root / job_name
            try:
                cleanup_trial_names = tuple(
                    sorted(
                        item.name
                        for item in cleanup_job.iterdir()
                        if item.is_dir() and not item.is_symlink()
                    )
                )
            except OSError:
                cleanup_trial_names = ()
            try:
                process = self._process_runner(
                    command,
                    cwd=self._repository_root,
                    environment=environment,
                    deadline_monotonic=request.global_deadline_monotonic,
                    timeout_seconds=(
                        request.specification.limits.timeout_seconds
                        + self._attempt_envelope_grace_seconds
                    ),
                    disk_free=lambda: self._disk_free(self._evaluation_root),
                    safe_stop_free_bytes=SAFE_STOP_FREE_BYTES,
                )
            finally:
                try:
                    trial_name = _trial_name_hint(job_root, job_name)
                finally:
                    with suppress(OSError):
                        cleanup_trial_names = tuple(
                            sorted(
                                item.name
                                for item in cleanup_job.iterdir()
                                if item.is_dir() and not item.is_symlink()
                            )
                        )
                    for cleanup_trial_name in cleanup_trial_names or (trial_name,):
                        self._containers.cleanup_new(
                            before,
                            expected_image_id=expected_image,
                            job_name=job_name,
                            trial_name=cleanup_trial_name,
                        )
            _assert_private_values_absent(
                job_root / job_name,
                auth_path=self._auth_file,
                markers=auth_markers,
                proxy_environment=self._proxy_environment,
                explicit_proxy_endpoints=self._explicit_proxy_endpoints,
                process=process,
            )
            if process.status == "safe_stopped":
                raise ControlledActiveSafeStop("disk_safe_stop_below_8_gib")
            if (
                process.status == "timed_out"
                and process.finished_monotonic >= request.global_deadline_monotonic
            ):
                raise ControlledActiveSafeStop("hard_wall_deadline_elapsed")
            try:
                try:
                    trial_result = _trial_result(job_root, job_name)
                except LiveAttemptError as exc:
                    raise LiveAttemptError(str(exc), diagnostic_code="trial-tree-rejected") from exc
                trial = trial_result[0] if trial_result is not None else None
                result = trial_result[1] if trial_result is not None else None
                if result is not None:
                    try:
                        job_config = _object(
                            job_root / job_name / "config.json", label="Harbor job config"
                        )
                        _validate_job_config(
                            job_config,
                            request,
                            task_root=task_root,
                            job_root=job_root,
                            job_name=job_name,
                            proxy_environment=self._proxy_environment,
                        )
                    except LiveAttemptError as exc:
                        raise LiveAttemptError(
                            str(exc), diagnostic_code="job-config-authority-rejected"
                        ) from exc
                try:
                    classification = _classify_preterminal(
                        process,
                        result,
                        request,
                        task,
                        task_root=task_root,
                        job_root=job_root,
                        job_name=job_name,
                        task_checksum=task_checksum,
                        attempt_envelope_timeout_seconds=(
                            request.specification.limits.timeout_seconds
                            + self._attempt_envelope_grace_seconds
                        ),
                    )
                except LiveAttemptError as exc:
                    raise LiveAttemptError(
                        str(exc),
                        diagnostic_code=(exc.diagnostic_code or "preterminal-structure-rejected"),
                    ) from exc
                if classification is not None:
                    category, retry, self._diagnostic_code = classification
                    return _lifecycle_attempt(
                        request, process, category=category, retry_eligible=retry
                    )
                if process.exit_code != 0 or trial is None or result is None:
                    raise LiveAttemptError("Harbor did not publish one terminal Trial result")
                if (
                    result.get("agent_result") is None
                    or result.get("verifier_result") is None
                    or (result.get("exception_info") is not None)
                ):
                    raise LiveAttemptError("terminal Harbor result is incomplete")
                repair = _result_from_job(request, task, trial)
                observation = _runtime_observation(
                    request,
                    task,
                    command,
                    task_root=task_root,
                    job_root=job_root,
                    job_name=job_name,
                    proxy_environment=self._proxy_environment,
                    result=result,
                    task_checksum=task_checksum,
                )
            except LiveAttemptError as exc:
                if not self._close_unusable_runtime_evidence:
                    raise
                self._diagnostic_code = exc.diagnostic_code or "strict-runtime-evidence-rejected"
                # A closed process whose outputs passed the private-value scan but cannot
                # satisfy the exact Harbor/result authority is terminal, unusable evidence.
                # Publishing a non-retry lifecycle Attempt closes the durable claim without
                # relabeling the event as an Agent observation or weakening strict validation.
                return _lifecycle_attempt(
                    request,
                    process,
                    category="runtime_pre_terminal_failure",
                    retry_eligible=False,
                )
            raw = canonical_json_bytes(repair.model_dump(mode="json"))
            source_attempt_id = canonical_content_id(
                {
                    "process_receipt_sha256": process.receipt_sha256,
                    "result_id": repair.result_id,
                    "trial_id": request.trial_id,
                },
                excluded=frozenset(),
            )
            evaluation_output = (
                self._evaluation_root / request.trial_id / f"{request.ordinal:04d}-evaluation"
            )
            evaluation_output.parent.mkdir(parents=True, exist_ok=True)
            package = evaluate_repair_result_package(
                task=task,
                tasks=self._task_suite,
                result=repair,
                source_attempt_id=source_attempt_id,
                output=evaluation_output,
            )
            return materialize_controlled_attempt(
                {
                    "schema_version": "cernora.reference.controlled-attempt/v1",
                    "trial_id": request.trial_id,
                    "ordinal": request.ordinal,
                    "predecessor_attempt_id": request.predecessor_attempt_id,
                    "source_attempt_id": source_attempt_id,
                    "source_manifest_sha256": canonical_content_id(
                        {
                            "process_receipt_sha256": process.receipt_sha256,
                            "repair_result_sha256": sha256_bytes(raw),
                            "runtime_observation_id": observation.observation_id,
                        },
                        excluded=frozenset(),
                    ),
                    "retry_eligible": False,
                    "resources": BatchAttemptResources(
                        duration_milliseconds=max(
                            0,
                            int((process.finished_monotonic - process.started_monotonic) * 1000),
                        )
                    ).model_dump(mode="json"),
                    "runtime_observation": observation.model_dump(mode="json"),
                    "repair_result": repair.model_dump(mode="json"),
                    "evaluation": package.model_dump(mode="python"),
                    "lifecycle": None,
                }
            )
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


__all__ = [
    "ContainerController",
    "ContainerRecord",
    "ContainerSnapshot",
    "ControlledHarborAttemptExecutor",
    "DockerContainerController",
    "LiveAttemptError",
    "attributable_container_ids",
    "compose_controlled_instruction",
    "validate_installed_harbor_cli",
]
