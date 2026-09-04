#!/usr/bin/env python3
"""Prepare, inspect, or step the exact Priority 4 development-only Agent pilot."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.controlled_live_attempt import ControlledHarborAttemptExecutor
from cernora_reference_workflow.development_agent_pilot import DevelopmentAgentPilotPlan
from cernora_reference_workflow.development_pilot_bundle import (
    DevelopmentPilotAuthorizationRequest,
    inspect_development_pilot_bundle,
    verify_development_pilot_runtime,
)
from cernora_reference_workflow.development_pilot_execution import (
    AmbiguousDevelopmentPilotAttempt,
    DevelopmentPilotStepResult,
    DevelopmentPilotStopped,
    inspect_development_pilot_execution,
    prepare_development_pilot_execution,
    step_development_pilot_execution,
    summarize_development_pilot_execution,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="create custody without external work")
    prepare.add_argument("bundle", type=Path)
    prepare.add_argument("--repository-root", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="strictly replay custody offline")
    inspect.add_argument("custody", type=Path)
    step = commands.add_parser("step", help="claim at most one external Agent Attempt")
    step.add_argument("custody", type=Path)
    step.add_argument("--repository-root", type=Path, required=True)
    step.add_argument("--accept-plan-id", required=True)
    step.add_argument("--accept-request-id", required=True)
    step.add_argument("--companion-wheel", type=Path, required=True)
    step.add_argument("--cernora-wheel", type=Path, required=True)
    return parser


def _result_bytes(result: DevelopmentPilotStepResult) -> bytes:
    return canonical_json_bytes(result.model_dump(mode="json"))


def _ensure_custody_parent(repository: Path) -> Path:
    parent = repository
    for name in (".agent", "custody"):
        candidate = parent / name
        candidate.mkdir(exist_ok=True)
        if (
            candidate.is_symlink()
            or not candidate.is_dir()
            or candidate.resolve(strict=True) != candidate
        ):
            raise ContractError("development pilot custody ancestors must be real directories")
        parent = candidate
    return parent


def _prepare(bundle: Path, repository_root: Path) -> DevelopmentPilotStepResult:
    manifest = inspect_development_pilot_bundle(bundle)
    plan = DevelopmentAgentPilotPlan.from_file(bundle / "plan.json")
    request = DevelopmentPilotAuthorizationRequest.model_validate_json(
        (bundle / "authorization-request.json").read_bytes()
    )
    if (
        manifest.plan_id != plan.plan_id
        or request.plan_id != plan.plan_id
        or plan.implementation_candidates != manifest.implementation_candidates
    ):
        raise ContractError("development pilot bundle Plan authority is inconsistent")
    root = repository_root.resolve(strict=True)
    custody_parent = _ensure_custody_parent(root)
    custody = custody_parent / f"development-pilot-{plan.plan_id}"
    if custody.relative_to(root).as_posix() != request.custody_subdirectory:
        raise ContractError("development pilot request custody path is inconsistent")
    return prepare_development_pilot_execution(
        plan,
        custody,
        authorization_request=request,
    )


def _inspect(custody: Path) -> DevelopmentPilotStepResult:
    return summarize_development_pilot_execution(custody)


def _step(
    custody: Path,
    repository_root: Path,
    accepted_plan_id: str,
    accepted_request_id: str,
    companion_wheel: Path,
    cernora_wheel: Path,
) -> DevelopmentPilotStepResult:
    state = inspect_development_pilot_execution(custody)
    repository = repository_root.resolve(strict=True)
    expected_custody = repository / ".agent" / "custody" / f"development-pilot-{state.plan.plan_id}"
    if custody.is_symlink() or custody.resolve(strict=True) != expected_custody:
        raise ContractError("development pilot step requires the exact authorized custody path")
    verify_development_pilot_runtime(
        state.plan,
        repository_root=repository,
        companion_wheel=companion_wheel,
        cernora_wheel=cernora_wheel,
    )
    auth_value = os.environ.get("PI_AUTH_JSON_PATH")
    if not auth_value:
        raise ContractError("development pilot requires explicit PI_AUTH_JSON_PATH")
    auth_file = Path(auth_value)
    assert state.plan.attempt_envelope_timeout_seconds is not None
    # Harbor's docker compose bind mounts are only reliable for evaluation
    # trees on paths shared with the Docker VM. macOS system temporary
    # directories such as the default TMPDIR are not, and bind sources there
    # silently lose container-side writes (the "pi session directory is
    # missing" failure). Keep the evaluation tree under an operator-owned,
    # worktree-external, VM-shared directory instead.
    evaluation_parent = Path.home() / ".cernora" / "pilot-evaluation"
    evaluation_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="cernora-development-pilot-evaluation-", dir=evaluation_parent
    ) as temporary:
        executor = ControlledHarborAttemptExecutor(
            repository_root=repository,
            tasks=state.plan.corpus.tasks,
            evaluation_root=Path(temporary),
            auth_file=auth_file,
            proxy_environment=os.environ,
            close_unusable_runtime_evidence=True,
            attempt_envelope_grace_seconds=(
                state.plan.attempt_envelope_timeout_seconds
                - state.plan.experiment_specs[0].limits.timeout_seconds
            ),
        )
        return step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=accepted_plan_id,
            accepted_request_id=accepted_request_id,
        )


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    previous_handlers: dict[signal.Signals, Any] = {}

    def interrupt(_signal_number: int, _frame: object) -> None:
        raise KeyboardInterrupt

    for signal_name in ("SIGTERM", "SIGHUP"):
        selected = getattr(signal, signal_name, None)
        if selected is not None:
            previous_handlers[selected] = signal.signal(selected, interrupt)
    try:
        if arguments.command == "prepare":
            result = _prepare(arguments.bundle, arguments.repository_root)
        elif arguments.command == "inspect":
            result = _inspect(arguments.custody)
        else:
            result = _step(
                arguments.custody,
                arguments.repository_root,
                arguments.accept_plan_id,
                arguments.accept_request_id,
                arguments.companion_wheel,
                arguments.cernora_wheel,
            )
    except AmbiguousDevelopmentPilotAttempt:
        print("error: development pilot Attempt state is ambiguous", file=sys.stderr)
        return 3
    except DevelopmentPilotStopped:
        print("error: development pilot stopped at a frozen safety bound", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print(
            "error: development pilot interrupted; active claim is not retryable",
            file=sys.stderr,
        )
        return 5
    except (ContractError, OSError, ValueError):
        print("error: development pilot operation failed", file=sys.stderr)
        return 1
    finally:
        for selected, handler in previous_handlers.items():
            signal.signal(selected, handler)
    print(_result_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
