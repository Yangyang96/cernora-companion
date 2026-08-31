#!/usr/bin/env python3
"""Prepare, inspect, or step the exact Priority 4 development-only Agent pilot."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.controlled_live_attempt import ControlledHarborAttemptExecutor
from cernora_reference_workflow.development_agent_pilot import DevelopmentAgentPilotPlan
from cernora_reference_workflow.development_pilot_bundle import (
    DevelopmentPilotAuthorizationRequest,
    inspect_development_pilot_bundle,
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
    return parser


def _result_bytes(result: DevelopmentPilotStepResult) -> bytes:
    return canonical_json_bytes(result.model_dump(mode="json"))


def _prepare(bundle: Path, repository_root: Path) -> DevelopmentPilotStepResult:
    manifest = inspect_development_pilot_bundle(bundle)
    plan = DevelopmentAgentPilotPlan.from_file(bundle / "plan.json")
    request = DevelopmentPilotAuthorizationRequest.model_validate_json(
        (bundle / "authorization-request.json").read_bytes()
    )
    if manifest.plan_id != plan.plan_id or request.plan_id != plan.plan_id:
        raise ContractError("development pilot bundle Plan authority is inconsistent")
    root = repository_root.resolve(strict=True)
    custody = root.joinpath(*request.custody_subdirectory.split("/"))
    custody.parent.mkdir(parents=True, exist_ok=True)
    return prepare_development_pilot_execution(plan, custody)


def _inspect(custody: Path) -> DevelopmentPilotStepResult:
    return summarize_development_pilot_execution(custody)


def _step(
    custody: Path, repository_root: Path, accepted_plan_id: str
) -> DevelopmentPilotStepResult:
    state = inspect_development_pilot_execution(custody)
    auth_value = os.environ.get("CODEX_AUTH_JSON_PATH")
    if not auth_value:
        raise ContractError("development pilot requires explicit CODEX_AUTH_JSON_PATH")
    auth_file = Path(auth_value)
    with tempfile.TemporaryDirectory(prefix="cernora-development-pilot-evaluation-") as temporary:
        executor = ControlledHarborAttemptExecutor(
            repository_root=repository_root.resolve(strict=True),
            tasks=state.plan.corpus.tasks,
            evaluation_root=Path(temporary),
            auth_file=auth_file,
            proxy_environment=os.environ,
        )
        return step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=accepted_plan_id,
        )


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
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
            )
    except AmbiguousDevelopmentPilotAttempt:
        print("error: development pilot Attempt state is ambiguous", file=sys.stderr)
        return 3
    except DevelopmentPilotStopped:
        print("error: development pilot stopped at a frozen safety bound", file=sys.stderr)
        return 4
    except (ContractError, OSError, ValueError):
        print("error: development pilot operation failed", file=sys.stderr)
        return 1
    print(_result_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
