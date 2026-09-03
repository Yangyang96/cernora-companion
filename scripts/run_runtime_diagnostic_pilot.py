#!/usr/bin/env python3
"""Propose, prepare, inspect, or step the one-shot Priority 4 Runtime diagnostic."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path
from typing import Any

from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.development_agent_pilot import DevelopmentAgentPilotPlan
from cernora_reference_workflow.runtime_diagnostic_pilot import (
    AmbiguousRuntimeDiagnosticAttempt,
    RuntimeDiagnosticAuthorizationRequest,
    RuntimeDiagnosticPilotPlan,
    RuntimeDiagnosticState,
    RuntimeDiagnosticStopped,
    create_runtime_diagnostic_proposal,
    inspect_runtime_diagnostic_pilot,
    inspect_runtime_diagnostic_proposal,
    prepare_runtime_diagnostic_pilot,
    runtime_diagnostic_custody_path,
    step_runtime_diagnostic_pilot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    propose = commands.add_parser("propose", help="create a closed offline authorization request")
    propose.add_argument("--source-plan", type=Path, required=True)
    propose.add_argument("--repository-root", type=Path, required=True)
    propose.add_argument("--cernora-wheel", type=Path, required=True)
    propose.add_argument("--companion-wheel", type=Path, required=True)
    propose.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="create custody after exact authorization")
    prepare.add_argument("proposal", type=Path)
    prepare.add_argument("--repository-root", type=Path, required=True)
    prepare.add_argument("--accept-plan-id", required=True)
    prepare.add_argument("--accept-request-id", required=True)
    inspect = commands.add_parser("inspect", help="strictly replay custody offline")
    inspect.add_argument("custody", type=Path)
    step = commands.add_parser("step", help="claim at most one external Runtime Attempt")
    step.add_argument("custody", type=Path)
    step.add_argument("--repository-root", type=Path, required=True)
    step.add_argument("--accept-plan-id", required=True)
    step.add_argument("--accept-request-id", required=True)
    step.add_argument("--cernora-wheel", type=Path)
    step.add_argument("--companion-wheel", type=Path)
    return parser


def _custody(repository: Path, plan_id: str) -> Path:
    return runtime_diagnostic_custody_path(repository, plan_id)


def _summary(
    state: RuntimeDiagnosticState
    | tuple[RuntimeDiagnosticPilotPlan, RuntimeDiagnosticAuthorizationRequest],
) -> bytes:
    if isinstance(state, tuple):
        plan, request = state
        return canonical_json_bytes(
            {
                "plan_id": plan.plan_id,
                "request_id": request.request_id,
                "status": request.status,
            }
        )
    return canonical_json_bytes(
        {
            "execution_id": state.record.execution_id,
            "outcome_id": None if state.outcome is None else state.outcome.outcome_id,
            "plan_id": state.plan.plan_id,
            "status": state.status,
        }
    )


def _propose(
    arguments: argparse.Namespace,
) -> tuple[RuntimeDiagnosticPilotPlan, RuntimeDiagnosticAuthorizationRequest]:
    source = DevelopmentAgentPilotPlan.from_file(arguments.source_plan)
    repository = arguments.repository_root.resolve(strict=True)
    return create_runtime_diagnostic_proposal(
        arguments.output,
        source_plan=source,
        cernora_wheel=arguments.cernora_wheel,
        companion_wheel=arguments.companion_wheel,
        repository_root=repository,
    )


def _prepare(arguments: argparse.Namespace) -> RuntimeDiagnosticState:
    plan, request = inspect_runtime_diagnostic_proposal(arguments.proposal)
    if (
        arguments.accept_plan_id != plan.plan_id
        or arguments.accept_request_id != request.request_id
    ):
        raise ContractError("Runtime diagnostic preparation lacks exact acceptance")
    custody = _custody(arguments.repository_root, plan.plan_id)
    return prepare_runtime_diagnostic_pilot(
        plan,
        request,
        custody,
        accepted_plan_id=arguments.accept_plan_id,
        accepted_request_id=arguments.accept_request_id,
    )


def _step(arguments: argparse.Namespace) -> RuntimeDiagnosticState:
    state = inspect_runtime_diagnostic_pilot(arguments.custody)
    repository = arguments.repository_root.resolve(strict=True)
    if arguments.custody.resolve(strict=True) != _custody(repository, state.plan.plan_id):
        raise ContractError("Runtime diagnostic step uses another custody path")
    auth_value = os.environ.get("PI_AUTH_JSON_PATH")
    if state.status == "prepared" and not auth_value:
        raise ContractError("Runtime diagnostic requires explicit PI_AUTH_JSON_PATH")
    return step_runtime_diagnostic_pilot(
        arguments.custody,
        repository_root=repository,
        cernora_wheel=arguments.cernora_wheel,
        companion_wheel=arguments.companion_wheel,
        auth_file=None if auth_value is None else Path(auth_value),
        proxy_environment=os.environ,
        accepted_plan_id=arguments.accept_plan_id,
        accepted_request_id=arguments.accept_request_id,
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
    result: (
        RuntimeDiagnosticState
        | tuple[
            RuntimeDiagnosticPilotPlan,
            RuntimeDiagnosticAuthorizationRequest,
        ]
    )
    try:
        if arguments.command == "propose":
            result = _propose(arguments)
        elif arguments.command == "prepare":
            result = _prepare(arguments)
        elif arguments.command == "inspect":
            result = inspect_runtime_diagnostic_pilot(arguments.custody)
        else:
            result = _step(arguments)
    except AmbiguousRuntimeDiagnosticAttempt:
        print(
            "error: Runtime diagnostic Attempt is ambiguous and cannot be retried",
            file=sys.stderr,
        )
        return 3
    except RuntimeDiagnosticStopped:
        print("error: Runtime diagnostic stopped at a frozen safety bound", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("error: Runtime diagnostic interrupted; no retry is authorized", file=sys.stderr)
        return 5
    except (ContractError, OSError, ValueError):
        print("error: Runtime diagnostic operation failed", file=sys.stderr)
        return 1
    finally:
        for selected, handler in previous_handlers.items():
            signal.signal(selected, handler)
    print(_summary(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
