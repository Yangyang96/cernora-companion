#!/usr/bin/env python3
"""Out-of-band Priority 4 Agent probe: run Harbor directly and keep everything.

This is NOT a Study, development-pilot, smoke, or runtime-diagnostic authority
run. It produces no Candidate, held-out, or Study evidence; its only purpose is
engineering diagnosis of the live Agent chain.

Materialization and the Harbor argv mirror the exact frozen
``RuntimeDiagnosticPilotPlan`` task and specification. The raw stdout, stderr,
exit code and complete Harbor job tree are retained in a git-ignored probe
directory under ``.agent/`` so the closed result shape can be replayed offline.

Proxy endpoints and authentication are read ONLY from the operator environment
at run time and are never written into this file, the probe record, or any
tracked artifact. Run with ``--dry-run`` to verify assembly without secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from cernora_reference_workflow.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_execution import (  # noqa: E402
    ControlledAttemptRequest,
)
from cernora_reference_workflow.controlled_live_attempt import (  # noqa: E402
    _CHILD_ENV_ALLOWLIST,
    AGENT_IMPORT,
    _materialize_task,
)
from cernora_reference_workflow.controlled_run_plan import (  # noqa: E402
    ControlledTrialSlotV2,
)
from cernora_reference_workflow.runtime_diagnostic_pilot import (  # noqa: E402
    RuntimeDiagnosticPilotPlan,
)
from cernora_reference_workflow.runtime_policy import (  # noqa: E402
    PROVIDER_PROXY_INPUT_NAMES,
    resolve_provider_proxy_configuration,
)

DEFAULT_PLAN = (
    REPOSITORY_ROOT
    / "preparations/next-priority4-runtime-timeout-classification-confirmation/plan.json"
)
HARBOR = REPOSITORY_ROOT / ".venv" / "bin" / "harbor"
DEFAULT_AGENT_TIMEOUT_SECONDS = 300
ENVELOPE_GRACE_SECONDS = 60


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--agent-timeout-seconds", type=int, default=DEFAULT_AGENT_TIMEOUT_SECONDS)
    parser.add_argument("--envelope-grace-seconds", type=int, default=ENVELOPE_GRACE_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _task_toml(image_digest: str, agent_timeout_seconds: int) -> str:
    timeout = float(agent_timeout_seconds)
    return (
        'schema_version = "1.3"\n\n'
        "[metadata]\n"
        'author_name = "Cernora contributors"\n'
        'author_email = "noreply@example.invalid"\n'
        'difficulty = "hard"\n'
        'category = "software-engineering"\n'
        'tags = ["python", "repair", "deterministic"]\n\n'
        "[verifier]\n"
        f"timeout_sec = {timeout!r}\n\n"
        "[agent]\n"
        f"timeout_sec = {timeout!r}\n\n"
        "[environment]\n"
        f'docker_image = "sha256:{image_digest}"\n'
        'workdir = "/workspace"\n'
        'network_mode = "public"\n'
    )


def _materialize(
    plan: RuntimeDiagnosticPilotPlan,
    task_root: Path,
    agent_timeout_seconds: int,
) -> ControlledAttemptRequest:
    task = plan.task
    spec = plan.specification
    zero64 = "0" * 64
    slot = ControlledTrialSlotV2(
        schema_version="cernora.reference.controlled-trial-slot/v2",
        trial_slot_id=zero64,
        run_plan_id=zero64,
        slot_index=1,
        case_id=task.case.case_id,
        configuration_id=spec.configuration_id,
        experiment_id=zero64,
        repetition=1,
    )
    request = ControlledAttemptRequest(
        trial_id="0" * 32,
        slot=slot,
        specification=spec,
        ordinal=1,
        predecessor_attempt_id=None,
        global_deadline_monotonic=time.monotonic() + 86400,
    )
    _materialize_task(request, task, task_root)
    image_digest = spec.container.image.rsplit("@sha256:", 1)[1]
    (task_root / "task.toml").write_text(
        _task_toml(image_digest, agent_timeout_seconds), encoding="utf-8"
    )
    return request


def _command(
    request: ControlledAttemptRequest,
    task_root: Path,
    job_root: Path,
    job_name: str,
) -> tuple[str, ...]:
    spec = request.specification
    if spec.limits.cpu_millis < 1000 or spec.limits.cpu_millis % 1000:
        raise ContractError("Harbor CPU override cannot exactly represent limits")
    # Same argv as the controlled executor except --delete is intentionally
    # omitted so the complete Harbor job tree is retained for diagnosis.
    return (
        str(HARBOR),
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
        "1",
        "--override-cpus",
        str(spec.limits.cpu_millis // 1000),
        "--override-memory-mb",
        str(spec.limits.memory_mebibytes),
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
    )


def _child_environment(auth_path: Path) -> dict[str, str]:
    ambient = os.environ
    child = {key: ambient[key] for key in _CHILD_ENV_ALLOWLIST if ambient.get(key)}
    child["CODEX_AUTH_JSON_PATH"] = str(auth_path)
    proxy_inputs = {name: ambient[name] for name in PROVIDER_PROXY_INPUT_NAMES if name in ambient}
    child.update(resolve_provider_proxy_configuration(proxy_inputs).environment)
    return child


def _tree_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    if not root.is_dir():
        return manifest
    for path in sorted(root.rglob("*")):
        if path.is_file():
            manifest[str(path.relative_to(root))] = sha256_bytes(path.read_bytes())
    return manifest


def _record(output: Path, payload: dict[str, object]) -> None:
    (output / "record.json").write_bytes(canonical_json_bytes(payload))


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        plan = RuntimeDiagnosticPilotPlan.from_bytes(
            arguments.plan.resolve(strict=True).read_bytes()
        )
    except (ContractError, OSError, ValueError) as exc:
        print(f"error: cannot load the frozen probe plan: {exc}", file=sys.stderr)
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output = REPOSITORY_ROOT / ".agent" / f"probe-{stamp}"
    task_root = output / "task"
    job_root = output / "job"
    task_root.mkdir(parents=True)
    job_root.mkdir()
    request = _materialize(plan, task_root, arguments.agent_timeout_seconds)
    job_name = f"probe-{stamp}"
    command = _command(request, task_root, job_root, job_name)

    if arguments.dry_run:
        print(" ".join(command))
        print(f"output: {output.relative_to(REPOSITORY_ROOT)}")
        print(
            "expected env: CODEX_AUTH_JSON_PATH plus proxy inputs "
            + ", ".join(PROVIDER_PROXY_INPUT_NAMES)
        )
        return 0

    auth_value = os.environ.get("CODEX_AUTH_JSON_PATH")
    if not auth_value:
        print(
            "error: probe requires CODEX_AUTH_JSON_PATH in the operator environment",
            file=sys.stderr,
        )
        return 2
    auth_path = Path(auth_value)
    if not auth_path.is_absolute() or not auth_path.is_file():
        print(
            "error: CODEX_AUTH_JSON_PATH must be an absolute regular file",
            file=sys.stderr,
        )
        return 2
    try:
        environment = _child_environment(auth_path)
    except ContractError as exc:
        print(f"error: proxy environment is incomplete: {exc}", file=sys.stderr)
        return 2

    started_monotonic = time.monotonic()
    started_wall = datetime.now(UTC).isoformat()
    envelope = arguments.agent_timeout_seconds + arguments.envelope_grace_seconds
    stdout_path = output / "stdout.log"
    stderr_path = output / "stderr.log"
    exit_code: int | None = None
    try:
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                list(command),
                env=environment,
                cwd=REPOSITORY_ROOT,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            try:
                process.communicate(timeout=envelope + 120)
                exit_code = process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                exit_code = None
    finally:
        finished_monotonic = time.monotonic()
        finished_wall = datetime.now(UTC).isoformat()
    _record(
        output,
        {
            "schema_version": "cernora.reference.out-of-band-probe-record/v1",
            "plan_id": plan.plan_id,
            "case_id": plan.task.case.case_id,
            "agent_timeout_seconds": arguments.agent_timeout_seconds,
            "envelope_grace_seconds": arguments.envelope_grace_seconds,
            "started_wall": started_wall,
            "finished_wall": finished_wall,
            "duration_seconds": round(finished_monotonic - started_monotonic, 3),
            "exit_code": exit_code,
            "timed_out_killed": exit_code is None,
            "command": list(command),
            "stdout_sha256": sha256_bytes(stdout_path.read_bytes()),
            "stderr_sha256": sha256_bytes(stderr_path.read_bytes()),
            "job_tree": _tree_manifest(job_root),
        },
    )
    print(
        json.dumps(
            {"output": str(output.relative_to(REPOSITORY_ROOT)), "exit_code": exit_code},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
