"""Run one explicit authenticated Harbor/Codex tracer and freeze its evidence."""

from __future__ import annotations

import argparse
import inspect
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from cernora_reference_workflow.attempt_record import publish_preterminal_attempt
from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
    require_regular_file,
    sha256_file,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.freeze import AttemptCapture, RequestedState, freeze_attempt
from cernora_reference_workflow.lifecycle import (
    TerminalRecord,
    materialize_preterminal_record,
)
from cernora_reference_workflow.offline import evaluate_frozen_export
from cernora_reference_workflow.report import publish_run_report
from cernora_reference_workflow.report_builder import (
    build_run_report,
    build_unavailable_run_report,
)
from cernora_reference_workflow.retry import AttemptResult, run_with_frozen_retry
from cernora_reference_workflow.runtime_observation import (
    CONTAINER_CLEANUP_RECEIPT,
    ContainerImageObservation,
    inspect_runtime_artifacts,
)
from cernora_reference_workflow.runtime_policy import (
    RUNTIME_CLEANUP_RECEIPT,
    RUNTIME_POLICY,
    TELEMETRY_CONFIG_TOML,
    OperatorInterruptReceipt,
)
from cernora_reference_workflow.spec_builder import (
    DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
    TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    build_tiny_calculator_spec,
    build_tiny_calculator_v2_spec,
)
from cernora_reference_workflow.test_runner import ResourceReceipt

ROOT = Path(__file__).resolve().parents[1]
AGENT_IMPORT = "cernora_reference_workflow.runtime_agent:TelemetryDisabledCodex"
_TRANSIENT_PROVIDER_STATUS = re.compile(r"(?<!\d)(?:408|429|500|502|503|504)(?!\d)")
_TRANSIENT_PROVIDER_MARKERS = (
    "gateway",
    "provider",
    "rate limit",
    "service unavailable",
    "upstream",
)
_AUTH_REDACTION_PLACEHOLDER = b"<redacted-external-codex-auth>"
_AUTH_REDACTION_RECEIPT = "auth-redaction.json"


@dataclass(frozen=True)
class FrozenLiveAttempt:
    export_root: Path | None
    portable_export_path: str | None
    preterminal_manifest_sha256: str | None


def _object(path: Path) -> dict[str, Any]:
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise ContractError(f"{path.name} must contain one JSON object")
    return cast(dict[str, Any], payload)


def _integer_file(path: Path, *, minimum: int = 0) -> int:
    require_regular_file(path)
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (UnicodeDecodeError, ValueError) as exc:
        raise ContractError(f"invalid integer receipt: {path.name}") from exc
    if value < minimum:
        raise ContractError(f"integer receipt is below its minimum: {path.name}")
    return value


def _auth_value_markers(auth_path: Path) -> tuple[bytes, ...]:
    payload = load_json_file(auth_path)
    if not isinstance(payload, dict):
        raise ContractError("external Codex auth file must contain one JSON object")
    sensitive_names = (
        "account",
        "email",
        "key",
        "organization",
        "secret",
        "subscription",
        "token",
    )
    markers: set[bytes] = set()

    def collect(value: object, *, sensitive: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                collect(
                    item,
                    sensitive=sensitive or any(name in key.lower() for name in sensitive_names),
                )
        elif isinstance(value, list):
            for item in value:
                collect(item, sensitive=sensitive)
        elif sensitive and isinstance(value, str) and len(value) >= 6:
            markers.add(value.encode("utf-8"))

    collect(payload)
    if not markers:
        raise ContractError("external Codex auth file has no verifiable sensitive value markers")
    return tuple(sorted(markers))


def _harbor_command(
    spec: ExperimentSpec,
    job_name: str,
    *,
    agent_timeout_multiplier: float,
) -> list[str]:
    return [
        str(ROOT / ".venv/bin/harbor"),
        "run",
        "-p",
        f"tasks/{spec.task.task_id}",
        "-a",
        AGENT_IMPORT,
        "-m",
        spec.runtime.model,
        "-e",
        "docker",
        "--ak",
        f"version={spec.runtime.version}",
        "--ak",
        f"reasoning_effort={spec.runtime.reasoning_effort}",
        "--ak",
        "reasoning_summary=none",
        "--ak",
        "web_search=disabled",
        "--ak",
        "strict_config=true",
        "--agent-setup-timeout-multiplier",
        "4",
        "--agent-timeout-multiplier",
        str(agent_timeout_multiplier),
        "--override-cpus",
        str(spec.limits.cpu_millis // 1000),
        "--override-memory-mb",
        str(spec.limits.memory_mebibytes),
        "--delete",
        "-o",
        "attempts",
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


def _running_trial_container(trial_name: str) -> str | None:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    normalized_trial = trial_name.lower().replace("_", "-")
    matches = [
        name
        for name in result.stdout.splitlines()
        if normalized_trial in name.lower().replace("_", "-")
    ]
    if len(matches) > 1:
        raise ContractError("operator interruption matched more than one trial container")
    return matches[0] if matches else None


def _is_codex_exec_argv(argv: tuple[bytes, ...]) -> bool:
    """Match the single pinned native ``codex exec`` process and no monitor command."""

    return len(argv) >= 2 and argv[0].rsplit(b"/", 1)[-1] == b"codex" and argv[1] == b"exec"


def _container_image_id(container: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.Image}}", container],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    image_id = result.stdout.strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise ContractError("Docker returned an invalid task image content ID")
    return image_id


def _verify_pinned_task_image(spec: ExperimentSpec) -> None:
    expected_image_id = f"sha256:{spec.container.image.rsplit('@sha256:', 1)[1]}"
    task_config = ROOT / "tasks" / spec.task.task_id / "task.toml"
    try:
        task_payload = tomllib.loads(task_config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ContractError("cannot load the pinned Harbor task configuration") from exc
    environment = task_payload.get("environment")
    if not isinstance(environment, dict) or environment.get("docker_image") != expected_image_id:
        raise ContractError("Harbor task does not select the ExperimentSpec task image")
    result = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            expected_image_id,
            "--format",
            "{{.Id}} {{.Os}}/{{.Architecture}}",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ContractError("pinned local task image is unavailable")
    if result.stdout.strip() != f"{expected_image_id} {spec.container.platform}":
        raise ContractError("local task image identity or platform does not match ExperimentSpec")


def _observe_runtime_container(
    job: Path,
    *,
    stopped: threading.Event,
    operator_interrupt: bool,
) -> str | None:
    deadline = time.monotonic() + 1800
    trial: Path | None = None
    while time.monotonic() < deadline and not stopped.is_set():
        if not job.is_dir():
            stopped.wait(0.25)
            continue
        trials = tuple(child for child in job.iterdir() if child.is_dir())
        if len(trials) > 1:
            raise ContractError("Runtime monitor observed more than one live trial")
        if trials:
            trial = trials[0]
            break
        stopped.wait(0.25)
    if trial is None:
        if operator_interrupt and not stopped.is_set():
            raise ContractError("operator interruption never observed the Codex run boundary")
        return None

    matcher_source = inspect.getsource(_is_codex_exec_argv)
    signal_program = f"""import os
import signal

{matcher_source}

targets = []
for entry in os.listdir('/proc'):
    if not entry.isdigit():
        continue
    if int(entry) == os.getpid():
        continue
    try:
        raw_argv = open(f'/proc/{{entry}}/cmdline', 'rb').read()
        argv = [part for part in raw_argv.split(b'\\0') if part]
    except OSError:
        continue
    if _is_codex_exec_argv(tuple(argv)):
        targets.append(int(entry))
if len(targets) != 1:
    raise SystemExit(f'expected exactly one active codex exec process; observed {{len(targets)}}')
os.kill(targets[0], signal.SIGINT)
print(1)
"""
    while time.monotonic() < deadline and not stopped.is_set():
        container = _running_trial_container(trial.name)
        if container is None:
            stopped.wait(0.25)
            continue
        image_id = _container_image_id(container)
        if not operator_interrupt:
            return image_id
        if not (trial / "agent/effective-config.toml").is_file():
            stopped.wait(0.25)
            continue
        if stopped.wait(2):
            return image_id
        result = subprocess.run(
            ["docker", "exec", container, "python", "-c", signal_program],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            matched = int(result.stdout.strip())
        except ValueError:
            matched = 0
        if result.returncode == 0 and matched > 0:
            (trial / "operator-interrupt.json").write_bytes(
                canonical_json_bytes(
                    OperatorInterruptReceipt(
                        operator_signal="SIGINT",
                        schema_version="cernora.reference.operator-interrupt/v1",
                        target="active-codex-process",
                        verified_signal_count=1,
                    ).model_dump(mode="json")
                )
            )
            return image_id
        stopped.wait(0.25)
    if operator_interrupt and not stopped.is_set():
        raise ContractError("operator interruption could not signal the active Codex process")
    return None


def _run_harbor(
    command: list[str],
    *,
    env: dict[str, str],
    job: Path,
    operator_interrupt: bool,
) -> tuple[int, str | None]:
    try:
        process = subprocess.Popen(command, cwd=ROOT, env=env)
    except OSError:
        return 127, None
    errors: list[BaseException] = []
    image_ids: list[str] = []
    stopped = threading.Event()

    def monitor() -> None:
        try:
            image_id = _observe_runtime_container(
                job,
                stopped=stopped,
                operator_interrupt=operator_interrupt,
            )
            if image_id is not None:
                image_ids.append(image_id)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=monitor, name="codex-runtime-monitor", daemon=True)
    thread.start()
    return_code = process.wait()
    stopped.set()
    thread.join(timeout=5)
    if thread.is_alive():
        raise ContractError("operator interruption monitor did not terminate")
    if errors and (operator_interrupt or return_code == 0):
        raise ContractError(f"Runtime monitor failed: {errors[0]}") from errors[0]
    if len(image_ids) > 1:
        raise ContractError("Runtime monitor observed conflicting task image identities")
    return return_code, image_ids[0] if image_ids else None


def _trial_directory(job: Path) -> Path:
    trials = tuple(
        child for child in job.iterdir() if child.is_dir() and (child / "result.json").is_file()
    )
    if len(trials) != 1:
        raise ContractError("Harbor job must contain exactly one immutable trial")
    return trials[0]


def _optional_trial_directory(job: Path) -> Path | None:
    if not job.is_dir():
        return None
    trials = tuple(
        child for child in job.iterdir() if child.is_dir() and (child / "result.json").is_file()
    )
    if len(trials) > 1:
        raise ContractError("Harbor job must contain at most one immutable trial")
    return trials[0] if trials else None


def _verify_runtime_policy(trial: Path) -> None:
    effective = trial / "agent/effective-config.toml"
    effective_features = trial / "agent/effective-features.txt"
    policy = trial / "agent/runtime-policy.json"
    cleanup = trial / "agent/runtime-cleanup.json"
    for path in (effective, effective_features, policy, cleanup):
        require_regular_file(path)
    if effective.read_text(encoding="utf-8") != TELEMETRY_CONFIG_TOML:
        raise ContractError("observed Codex config does not match the telemetry-off policy")
    if policy.read_bytes() != canonical_json_bytes(RUNTIME_POLICY):
        raise ContractError("observed Runtime policy receipt mismatch")
    feature_states = {
        fields[0]: fields[-1]
        for line in effective_features.read_text(encoding="utf-8").splitlines()
        if len(fields := line.split()) >= 3
    }
    if feature_states.get("plugins") != "false":
        raise ContractError("observed Codex plugin feature state is not disabled")
    if feature_states.get("unified_exec") != "true":
        raise ContractError("observed Codex unified-exec feature state is not enabled")
    if cleanup.read_bytes() != canonical_json_bytes(RUNTIME_CLEANUP_RECEIPT):
        raise ContractError("ephemeral Codex auth cleanup was not verified")


def _file_contains_marker(path: Path, markers: tuple[bytes, ...]) -> bool:
    longest = max(len(marker) for marker in markers)
    overlap = b""
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            data = overlap + chunk
            if any(marker in data for marker in markers):
                return True
            overlap = data[-(longest - 1) :] if longest > 1 else b""
    return False


def _assert_auth_absent(
    root: Path,
    auth_path: Path,
    auth_markers: tuple[bytes, ...],
) -> None:
    markers = (str(auth_path).encode("utf-8"), *auth_markers)
    for relative, path in closed_regular_tree(root).items():
        if path.name == "auth.json":
            raise ContractError(f"Harbor output retained prohibited auth file at {relative}")
        if _file_contains_marker(path, markers):
            raise ContractError(f"authentication value leaked into retained artifact: {relative}")


def _atomic_replace_bytes(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.auth-redaction-",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_new_bytes(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _sanitize_auth_artifacts(
    root: Path,
    auth_path: Path,
    auth_markers: tuple[bytes, ...],
) -> None:
    """Redact exact auth markers only from non-evidence Harbor log files."""

    files = closed_regular_tree(root)
    if _AUTH_REDACTION_RECEIPT in files:
        raise ContractError("Harbor output already contains an auth-redaction receipt")
    prohibited = [relative for relative, path in files.items() if path.name == "auth.json"]
    if prohibited:
        raise ContractError(f"Harbor output retained prohibited auth file at {prohibited[0]}")

    markers = tuple(
        sorted(
            {str(auth_path).encode("utf-8"), *auth_markers},
            key=lambda marker: (-len(marker), marker),
        )
    )
    replacements: list[tuple[str, Path, bytes, int]] = []
    for relative, path in files.items():
        data = path.read_bytes()
        count = 0
        for marker in markers:
            occurrences = data.count(marker)
            if occurrences:
                data = data.replace(marker, _AUTH_REDACTION_PLACEHOLDER)
                count += occurrences
        if count:
            parts = Path(relative).parts
            is_non_evidence_log = relative == "job.log" or (
                len(parts) == 2 and parts[1] == "trial.log"
            )
            if not is_non_evidence_log:
                raise ContractError(
                    f"authentication value leaked into exportable or non-log artifact: {relative}"
                )
            replacements.append((relative, path, data, count))

    redacted_files: list[dict[str, object]] = []
    for relative, path, data, count in replacements:
        _atomic_replace_bytes(path, data)
        redacted_files.append({"path": relative, "redaction_count": count})

    receipt = {
        "schema_version": "cernora.reference.auth-redaction/v1",
        "placeholder": _AUTH_REDACTION_PLACEHOLDER.decode("ascii"),
        "files": redacted_files,
    }
    _write_new_bytes(root / _AUTH_REDACTION_RECEIPT, canonical_json_bytes(receipt))


def _verify_container_cleanup(trial_name: str) -> None:
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    normalized = trial_name.lower().replace("_", "-")
    if any(normalized in name.lower().replace("_", "-") for name in result.stdout.splitlines()):
        raise ContractError("Harbor trial container remains after cleanup")


def _sanitized_runtime_files(
    trial: Path,
    spec: ExperimentSpec,
    *,
    agent_timeout_multiplier: float,
    operator_interrupt: bool,
    observed_image_id: str,
) -> tuple[tuple[str, Path], ...]:
    result = _object(trial / "result.json")
    agent_info = result.get("agent_info")
    if not isinstance(agent_info, dict):
        raise ContractError("Harbor result is missing agent_info")
    observed_model = agent_info.get("model_info")
    if not isinstance(observed_model, dict):
        raise ContractError("Harbor result is missing model_info")
    if (
        agent_info.get("name") != "codex"
        or agent_info.get("version") != spec.runtime.version
        or observed_model.get("name") != spec.runtime.model
        or result.get("task_name") != spec.task.task_id
    ):
        raise ContractError("Harbor result does not match the pinned Runtime identity")
    normalized = trial / "normalized-runtime"
    normalized.mkdir()
    config_payload = {
        "schema_version": "cernora.reference.harbor-config-observation/v1",
        "agent": AGENT_IMPORT,
        "agent_setup_timeout_seconds": spec.limits.agent_setup_timeout_seconds,
        "agent_timeout_multiplier": agent_timeout_multiplier,
        "cpu_millis": spec.limits.cpu_millis,
        "delete_environment": True,
        "force_build": False,
        "harbor_version": spec.harness.version,
        "memory_mebibytes": spec.limits.memory_mebibytes,
        "model": spec.runtime.model,
        "native_retries": 0,
        "operator_interrupt": operator_interrupt,
        "reasoning_effort": spec.runtime.reasoning_effort,
        "runtime_policy": RUNTIME_POLICY,
        "task_id": spec.task.task_id,
        "timeout_seconds": spec.limits.timeout_seconds,
        "web_search": False,
    }
    result_payload = {
        "schema_version": "cernora.reference.harbor-result-observation/v1",
        "agent_name": agent_info.get("name"),
        "agent_version": agent_info.get("version"),
        "environment_cleanup_verified": True,
        "exception_type": (
            result.get("exception_info", {}).get("exception_type")
            if isinstance(result.get("exception_info"), dict)
            else None
        ),
        "model": observed_model.get("name"),
        "task_checksum": result.get("task_checksum"),
        "task_name": result.get("task_name"),
    }
    config_path = normalized / "harbor-trial-config.json"
    result_path = normalized / "harbor-trial-result.json"
    container_cleanup_path = normalized / "container-cleanup.json"
    container_image_path = normalized / "container-image.json"
    config_path.write_bytes(canonical_json_bytes(config_payload))
    result_path.write_bytes(canonical_json_bytes(result_payload))
    container_cleanup_path.write_bytes(canonical_json_bytes(CONTAINER_CLEANUP_RECEIPT))
    container_image_path.write_bytes(
        canonical_json_bytes(
            ContainerImageObservation(
                schema_version="cernora.reference.container-image-observation/v1",
                task_image_reference=spec.container.image,
                observed_image_id=observed_image_id,
                matched=True,
            ).model_dump(mode="json")
        )
    )
    runtime: list[tuple[str, Path]] = [
        ("runtime/harbor-trial-config.json", config_path),
        ("runtime/harbor-trial-result.json", result_path),
        ("runtime/codex-effective-config.toml", trial / "agent/effective-config.toml"),
        ("runtime/codex-effective-features.txt", trial / "agent/effective-features.txt"),
        ("runtime/runtime-policy.json", trial / "agent/runtime-policy.json"),
        ("runtime/runtime-cleanup.json", trial / "agent/runtime-cleanup.json"),
        ("runtime/container-cleanup.json", container_cleanup_path),
        ("runtime/container-image.json", container_image_path),
    ]
    inspected_artifacts: list[tuple[str, Path]] = []
    trajectory = trial / "agent/trajectory.json"
    if trajectory.is_file():
        runtime.append(("runtime/trajectory.json", trajectory))
        inspected_artifacts.append(("runtime/trajectory.json", trajectory))
    events = trial / "agent/codex.txt"
    if events.is_file():
        runtime.append(("runtime/codex-events.jsonl", events))
        inspected_artifacts.append(("runtime/codex-events.jsonl", events))
    sessions_root = trial / "agent/sessions"
    sessions = sorted(sessions_root.rglob("*.jsonl")) if sessions_root.is_dir() else []
    used_names: set[str] = set()
    for session in sessions:
        if session.name in used_names:
            raise ContractError("Codex session JSONL basenames are not unique")
        used_names.add(session.name)
        relative = f"runtime/codex-session/{session.name}"
        runtime.append((relative, session))
        inspected_artifacts.append((relative, session))
    interruption = trial / "operator-interrupt.json"
    if interruption.is_file():
        runtime.append(("runtime/operator-interrupt.json", interruption))
    observation = inspect_runtime_artifacts(
        tuple(inspected_artifacts),
        effective_config_sha256=sha256_file(trial / "agent/effective-config.toml"),
        effective_features_sha256=sha256_file(trial / "agent/effective-features.txt"),
        runtime_policy_sha256=sha256_file(trial / "agent/runtime-policy.json"),
        runtime_cleanup_sha256=sha256_file(trial / "agent/runtime-cleanup.json"),
        container_cleanup_sha256=sha256_file(container_cleanup_path),
    )
    observation_path = normalized / "runtime-boundary-observation.json"
    observation_path.write_bytes(canonical_json_bytes(observation.model_dump(mode="json")))
    runtime.append(("runtime/runtime-boundary-observation.json", observation_path))
    return tuple(runtime)


def _requested_state(result: dict[str, Any], return_code: int) -> RequestedState:
    exception = result.get("exception_info")
    exception_type = exception.get("exception_type") if isinstance(exception, dict) else None
    if exception_type == "AgentTimeoutError":
        return "timed-out"
    if return_code in {130, -2}:
        return "interrupted"
    if exception_type is not None or return_code != 0:
        raise ContractError(
            f"Harbor trial failed before a freezable terminal result: {exception_type}"
        )
    return "completed"


def _preterminal_state(
    result: dict[str, Any] | None,
    return_code: int,
) -> RequestedState | str | None:
    """Classify only failures that did not produce a normal timeout/interruption terminal."""

    if result is None:
        return "infrastructure-start-failure"
    exception = result.get("exception_info")
    exception_type = exception.get("exception_type") if isinstance(exception, dict) else None
    if exception_type == "AgentTimeoutError" or return_code in {130, -2}:
        return None
    if exception_type is None and return_code == 0:
        return None
    if result.get("agent_execution") is None:
        return "infrastructure-start-failure"
    message = exception.get("exception_message") if isinstance(exception, dict) else None
    normalized_message = message.lower() if isinstance(message, str) else ""
    transient_provider = (
        exception_type == "NonZeroAgentExitCodeError"
        and _TRANSIENT_PROVIDER_STATUS.search(normalized_message) is not None
        and any(marker in normalized_message for marker in _TRANSIENT_PROVIDER_MARKERS)
        and result.get("agent_result") is None
        and result.get("verifier_result") is None
    )
    if transient_provider:
        return "transient-provider-pre-terminal"
    return "runtime-pre-terminal-failure"


def _record_preterminal_attempt(
    *,
    job: Path,
    spec: ExperimentSpec,
    source_trial_id: str,
    state: str,
    predecessor_attempt_id: str | None,
) -> tuple[TerminalRecord, str]:
    if state not in {
        "infrastructure-start-failure",
        "transient-provider-pre-terminal",
        "runtime-pre-terminal-failure",
    }:
        raise ContractError("unsupported pre-terminal lifecycle state")
    terminal = materialize_preterminal_record(
        experiment_id=spec.experiment_id,
        source_trial_id=source_trial_id,
        state=cast(Any, state),
        predecessor_attempt_id=predecessor_attempt_id,
    )
    job.mkdir(parents=True, exist_ok=True)
    record_root = job / "reference-attempt"
    publish_preterminal_attempt(
        destination=record_root,
        experiment_id=spec.experiment_id,
        terminal=terminal,
        source_trial_id=source_trial_id,
    )
    return terminal, sha256_file(record_root / "manifest.json")


def _accepted_execution_policy(spec: ExperimentSpec) -> tuple[float, bool, str]:
    if spec == build_tiny_calculator_spec(ROOT):
        return DEFAULT_AGENT_TIMEOUT_MULTIPLIER, False, "examples/tiny-calculator-v1.json"
    timeout_spec = build_tiny_calculator_spec(
        ROOT,
        timeout_seconds=3,
        agent_timeout_multiplier=TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    )
    if spec == timeout_spec:
        return (
            TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
            False,
            "examples/tiny-calculator-v1-timeout.json",
        )
    if spec == build_tiny_calculator_spec(ROOT, operator_interrupt=True):
        return (
            DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
            True,
            "examples/tiny-calculator-v1-interruption.json",
        )
    if spec == build_tiny_calculator_v2_spec(ROOT):
        return DEFAULT_AGENT_TIMEOUT_MULTIPLIER, False, "examples/tiny-calculator-v2.json"
    raise ContractError("live tracer accepts only checked-in approved ExperimentSpec variants")


def _execute_live_attempt(
    *,
    ordinal: int,
    predecessor_attempt_id: str | None,
    base_job_name: str,
    spec: ExperimentSpec,
    auth_path: Path,
    auth_markers: tuple[bytes, ...],
    env: dict[str, str],
    agent_timeout_multiplier: float,
    operator_interrupt: bool,
) -> AttemptResult[FrozenLiveAttempt]:
    attempt_job_name = f"{base_job_name}-attempt-{ordinal}"
    job = ROOT / "attempts" / attempt_job_name
    export = ROOT / "exports" / attempt_job_name
    if job.exists() or job.is_symlink() or export.exists() or export.is_symlink():
        raise ContractError(f"immutable attempt output already exists: {attempt_job_name}")

    return_code, observed_image_id = _run_harbor(
        _harbor_command(
            spec,
            attempt_job_name,
            agent_timeout_multiplier=agent_timeout_multiplier,
        ),
        env=env,
        job=job,
        operator_interrupt=operator_interrupt,
    )
    trial = _optional_trial_directory(job)
    result = _object(trial / "result.json") if trial is not None else None
    source_trial_id_value = result.get("id") if result is not None else None
    source_trial_id = (
        source_trial_id_value
        if isinstance(source_trial_id_value, str) and source_trial_id_value
        else attempt_job_name
    )
    if job.is_dir():
        _sanitize_auth_artifacts(job, auth_path, auth_markers)
        _assert_auth_absent(job, auth_path, auth_markers)

    preterminal_state = None if operator_interrupt else _preterminal_state(result, return_code)
    if preterminal_state == "transient-provider-pre-terminal" and trial is not None:
        try:
            _verify_runtime_policy(trial)
        except ContractError:
            preterminal_state = "runtime-pre-terminal-failure"
    if preterminal_state is not None:
        if trial is not None and result is not None:
            _verify_container_cleanup(str(result.get("trial_name", trial.name)))
        terminal, preterminal_manifest_sha256 = _record_preterminal_attempt(
            job=job,
            spec=spec,
            source_trial_id=source_trial_id,
            state=preterminal_state,
            predecessor_attempt_id=predecessor_attempt_id,
        )
        return AttemptResult(
            terminal=terminal,
            source_trial_id=source_trial_id,
            raw_attempt_root=job,
            value=FrozenLiveAttempt(
                export_root=None,
                portable_export_path=None,
                preterminal_manifest_sha256=preterminal_manifest_sha256,
            ),
        )

    if trial is None or result is None:
        raise ContractError("Harbor did not produce a trial for a terminal Agent attempt")
    requested_state: RequestedState = (
        "interrupted" if operator_interrupt else _requested_state(result, return_code)
    )
    _verify_runtime_policy(trial)
    _verify_container_cleanup(str(result.get("trial_name", trial.name)))
    if observed_image_id is None:
        raise ContractError("Runtime monitor did not observe the task image content ID")
    expected_image_id = spec.container.image.rsplit("@", 1)[-1]
    if observed_image_id != expected_image_id:
        raise ContractError("observed Docker task image does not match ExperimentSpec")

    verifier = trial / "verifier"
    exit_code = _integer_file(verifier / "exit-code.txt")
    duration = _integer_file(verifier / "duration-milliseconds.txt")
    capture = AttemptCapture(
        source_trial_id=source_trial_id,
        candidate_root=verifier / "candidate",
        test_stdout=verifier / "stdout.txt",
        test_stderr=verifier / "stderr.txt",
        test_exit_code=exit_code,
        resource_receipt=ResourceReceipt(
            schema_version="cernora.reference.resource-receipt/v1",
            duration_milliseconds=duration,
            peak_memory_bytes=None,
            cpu_milliseconds=None,
        ),
        runtime_files=_sanitized_runtime_files(
            trial,
            spec,
            agent_timeout_multiplier=agent_timeout_multiplier,
            operator_interrupt=operator_interrupt,
            observed_image_id=observed_image_id,
        ),
    )
    terminal = freeze_attempt(
        spec=spec,
        capture=capture,
        baseline_root=ROOT / "tasks" / spec.task.task_id / "environment",
        test_plan_path=ROOT / "tasks" / spec.task.task_id / "tests/test-plan.json",
        requested_state=requested_state,
        predecessor_attempt_id=predecessor_attempt_id,
        destination=export,
    )
    _assert_auth_absent(job, auth_path, auth_markers)
    _assert_auth_absent(export, auth_path, auth_markers)
    return AttemptResult(
        terminal=terminal,
        source_trial_id=source_trial_id,
        raw_attempt_root=job,
        value=FrozenLiveAttempt(
            export_root=export,
            portable_export_path=f"exports/{attempt_job_name}",
            preterminal_manifest_sha256=None,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--job-name")
    parser.add_argument("--operator-interrupt", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ContractError("live tracer supports only macOS on Apple Silicon")
    spec = ExperimentSpec.from_file(args.spec)
    agent_timeout_multiplier, operator_interrupt, portable_spec_path = _accepted_execution_policy(
        spec
    )
    if args.spec.resolve() != ROOT / portable_spec_path:
        raise ContractError("live tracer requires the exact checked-in ExperimentSpec path")
    if args.operator_interrupt is not operator_interrupt:
        raise ContractError("operator interruption flag must match the ExperimentSpec identity")
    _verify_pinned_task_image(spec)
    auth_value = os.environ.get("CODEX_AUTH_JSON_PATH")
    if not auth_value:
        raise ContractError("CODEX_AUTH_JSON_PATH must name the explicit external auth file")
    auth_path = Path(auth_value).resolve(strict=True)
    require_regular_file(auth_path)
    auth_markers = _auth_value_markers(auth_path)

    default_job = f"priority3-live-{spec.experiment_id[:12]}"
    job_name = args.job_name or default_job
    if not job_name.replace("-", "").isalnum():
        raise ContractError("job name must contain only letters, digits, and hyphens")
    offline = ROOT / "reports/private" / job_name
    if offline.exists() or offline.is_symlink():
        raise ContractError(f"immutable report output already exists: {offline.name}")
    (ROOT / "attempts").mkdir(exist_ok=True)
    (ROOT / "exports").mkdir(exist_ok=True)
    offline.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_FORCE_AUTH_JSON", None)
    env["CODEX_AUTH_JSON_PATH"] = str(auth_path)
    attempts = run_with_frozen_retry(
        lambda ordinal, predecessor: _execute_live_attempt(
            ordinal=ordinal,
            predecessor_attempt_id=predecessor,
            base_job_name=job_name,
            spec=spec,
            auth_path=auth_path,
            auth_markers=auth_markers,
            env=env,
            agent_timeout_multiplier=agent_timeout_multiplier,
            operator_interrupt=operator_interrupt,
        )
    )
    selected = attempts[-1]
    history = tuple(
        (
            attempt.terminal,
            attempt.source_trial_id,
            attempt.value.preterminal_manifest_sha256,
        )
        for attempt in attempts
    )
    if selected.value.export_root is None or selected.value.portable_export_path is None:
        offline.mkdir()
        report = build_unavailable_run_report(spec=spec, attempts=history)
        evaluation_outcome = "inconclusive"
    else:
        evaluation = evaluate_frozen_export(
            spec=spec,
            export_root=selected.value.export_root,
            output_root=offline,
        )
        report = build_run_report(
            spec=spec,
            export_root=selected.value.export_root,
            evaluation=evaluation,
            portable_spec_path=portable_spec_path,
            portable_export_path=selected.value.portable_export_path,
            portable_bundle_path=f"rebuild/{job_name}/adapted/bundle.json",
            portable_evaluation_path=f"rebuild/{job_name}/evaluated",
            prior_attempts=history[:-1],
        )
        evaluation_outcome = evaluation.receipt.case_outcome
    publish_run_report(report, offline / "report")
    _assert_auth_absent(offline, auth_path, auth_markers)
    print(
        json.dumps(
            {
                "attempt_ids": [attempt.terminal.attempt_id for attempt in attempts],
                "case_outcome": evaluation_outcome,
                "experiment_id": spec.experiment_id,
                "export": selected.value.portable_export_path,
                "jobs": [
                    attempt.raw_attempt_root.relative_to(ROOT).as_posix() for attempt in attempts
                ],
                "lifecycle": selected.terminal.state,
                "report": f"reports/private/{job_name}/report",
                "report_id": report.report_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0 if selected.value.export_root is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
