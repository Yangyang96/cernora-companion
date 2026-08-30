"""Controlled Study CLI plus read-only historical Priority 4 migration commands."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cernora_reference_workflow.batch_summary import summarize_execution_pack
from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    load_json_file,
    read_regular_file_bytes,
)
from cernora_reference_workflow.comparison_input import (
    ComparisonConfigurationError,
    compare_batch_summary,
)
from cernora_reference_workflow.controlled_study import (
    ExecutionOutcome,
    StudyIntent,
)
from cernora_reference_workflow.controlled_study import (
    advance as advance_study,
)
from cernora_reference_workflow.controlled_study import (
    prepare as prepare_study,
)
from cernora_reference_workflow.controlled_study import (
    rebuild as rebuild_study,
)
from cernora_reference_workflow.execution import rebuild_execution_pack
from cernora_reference_workflow.live_attempt import execute_qualified_live_attempt
from cernora_reference_workflow.run_plan import RunPlan
from cernora_reference_workflow.runner import resume_repeat, run_repeat


class _UsageError(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="experiment")
    commands = parser.add_subparsers(dest="command", required=True)
    study = commands.add_parser("study", help="durable Controlled Study interface")
    study_commands = study.add_subparsers(dest="study_command", required=True)
    study_prepare = study_commands.add_parser("prepare", help="freeze one Study without execution")
    study_prepare.add_argument("intent", type=Path)
    study_prepare.add_argument("--output", type=Path, required=True)
    study_advance = study_commands.add_parser(
        "advance",
        help="idempotently advance one Study by at most one external Attempt",
    )
    study_advance.add_argument("study_dir", type=Path)
    study_advance.add_argument("--directive", type=Path, required=True)
    study_rebuild = study_commands.add_parser(
        "rebuild",
        help="offline-verify and reproduce one terminal Study artifact",
    )
    study_rebuild.add_argument("artifact", type=Path)
    study_rebuild.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="strictly verify and print a RunPlan preflight")
    verify.add_argument("plan", type=Path)

    run = commands.add_parser("run", help="start one accepted RunPlan")
    run.add_argument("plan", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--accept-plan-id", required=True)

    resume = commands.add_parser("resume", help="resume one incomplete Execution")
    resume.add_argument("execution_dir", type=Path)

    rebuild = commands.add_parser("rebuild", help="offline-rebuild one Execution Pack")
    rebuild.add_argument("execution_pack", type=Path)
    rebuild.add_argument("--output", type=Path, required=True)
    summarize = commands.add_parser(
        "summarize", help="normalize one Execution Pack and publish a Core Batch Summary"
    )
    summarize.add_argument("execution_pack", type=Path)
    summarize.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser(
        "compare", help="assemble and publish one controlled Core Comparison"
    )
    compare.add_argument("batch_summary", type=Path)
    compare.add_argument("--run-plan", type=Path, required=True)
    compare.add_argument("--plan", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(canonical_json_bytes(payload).decode("utf-8"), end="")


def _require_new_directory(path: Path, *, include_pack_sidecar: bool = False) -> None:
    if not path.parent.is_dir() or path.exists() or path.is_symlink():
        raise _UsageError("output parent must exist and output must be new")
    if include_pack_sidecar:
        pack = path.with_name(f"{path.name}.pack")
        if pack.exists() or pack.is_symlink():
            raise _UsageError("output Execution Pack sidecar must be new")


def _preflight(plan: RunPlan) -> dict[str, Any]:
    return {
        "command": "verify",
        "run_plan_id": plan.run_plan_id,
        "connector": plan.connector.model_dump(mode="json"),
        "cases": [item.model_dump(mode="json") for item in plan.cases],
        "configurations": [item.model_dump(mode="json") for item in plan.configurations],
        "cells": [item.model_dump(mode="json") for item in plan.cells],
        "slots": [item.model_dump(mode="json") for item in plan.expand_trial_slots()],
        "planned_trial_count": plan.planned_trial_count,
        "worst_case_attempt_count": plan.worst_case_attempt_count,
        "max_attempt_count": plan.execution.max_attempt_count,
        "wall_budget_seconds": plan.execution.max_total_wall_time_seconds,
    }


def _load_study_intent(path: Path) -> StudyIntent:
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise ContractError("Study Intent must contain a JSON object")
    intent = StudyIntent.model_validate(payload)
    if read_regular_file_bytes(path) != canonical_json_bytes(intent.model_dump(mode="json")):
        raise ContractError("Study Intent must use canonical JSON")
    return intent


def _load_study_directive(path: Path) -> dict[str, object]:
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise ContractError("Study advance directive must contain a JSON object")
    return payload


@contextmanager
def _graceful_stop() -> Iterator[Callable[[], bool]]:
    """Convert SIGINT into a stop request consumed only at a Trial boundary."""

    requested = False
    previous = signal.getsignal(signal.SIGINT)

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal requested
        requested = True

    signal.signal(signal.SIGINT, request_stop)
    try:
        yield lambda: requested
    finally:
        signal.signal(signal.SIGINT, previous)


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "study":
        study_outcome: ExecutionOutcome
        if args.study_command == "prepare":
            _require_new_directory(args.output)
            study_outcome = prepare_study(_load_study_intent(args.intent), args.output)
        elif args.study_command == "advance":
            with _graceful_stop() as should_stop:
                study_outcome = advance_study(
                    args.study_dir,
                    _load_study_directive(args.directive),
                    executor=execute_qualified_live_attempt,
                    should_stop=should_stop,
                )
        elif args.study_command == "rebuild":
            _require_new_directory(args.output)
            artifact = rebuild_study(args.artifact, args.output)
            _emit(
                {
                    "artifact_id": artifact.artifact_id,
                    "command": "study.rebuild",
                    "kind": artifact.kind,
                    "output": str(args.output),
                    "status": artifact.terminal_status,
                }
            )
            return 0
        else:
            raise AssertionError("argparse accepted an unknown Study command")
        _emit(
            {
                "command": f"study.{args.study_command}",
                **study_outcome.model_dump(mode="json"),
            }
        )
        return 3 if study_outcome.status in {"paused", "terminated"} else 0
    if args.command == "verify":
        _emit(_preflight(RunPlan.from_file(args.plan)))
        return 0
    if args.command == "run":
        plan = RunPlan.from_file(args.plan)
        if args.accept_plan_id != plan.run_plan_id:
            raise _UsageError("--accept-plan-id must exactly match the verified RunPlan")
        _require_new_directory(args.output, include_pack_sidecar=True)
        with _graceful_stop() as should_stop:
            outcome = run_repeat(
                args.output,
                plan,
                execute_qualified_live_attempt,
                should_stop=should_stop,
            )
        _emit(
            {
                "command": "run",
                "execution_id": outcome.state.record.execution_id,
                "execution_root": str(args.output),
                "pack_root": str(outcome.pack_root) if outcome.pack_root is not None else None,
                "run_plan_id": plan.run_plan_id,
                "status": outcome.status,
            }
        )
        return 0 if outcome.status == "completed" else 3
    if args.command == "resume":
        with _graceful_stop() as should_stop:
            outcome = resume_repeat(
                args.execution_dir,
                execute_qualified_live_attempt,
                should_stop=should_stop,
            )
        _emit(
            {
                "command": "resume",
                "execution_id": outcome.state.record.execution_id,
                "execution_root": str(args.execution_dir),
                "pack_root": str(outcome.pack_root) if outcome.pack_root is not None else None,
                "run_plan_id": outcome.state.run_plan.run_plan_id,
                "status": outcome.status,
            }
        )
        return 0 if outcome.status == "completed" else 3
    if args.command == "rebuild":
        _require_new_directory(args.output)
        manifest = rebuild_execution_pack(args.execution_pack, args.output)
        _emit(
            {
                "command": "rebuild",
                "execution_id": manifest.execution_id,
                "output": str(args.output),
                "run_plan_id": manifest.run_plan_id,
                "status": "completed",
            }
        )
        return 0
    if args.command == "summarize":
        _require_new_directory(args.output)
        batch_summary = summarize_execution_pack(args.execution_pack, args.output)
        _emit(
            {
                "batch_input_id": batch_summary.batch_input_id,
                "command": "summarize",
                "execution_id": batch_summary.execution_id,
                "output": str(args.output),
                "run_plan_id": batch_summary.run_plan_id,
                "status": "completed",
                "summary_id": batch_summary.summary_id,
            }
        )
        return 0
    if args.command == "compare":
        _require_new_directory(args.output)
        comparison_summary = compare_batch_summary(
            args.batch_summary,
            args.run_plan,
            args.plan,
            args.output,
        )
        _emit(
            {
                "command": "compare",
                "comparison_id": comparison_summary.comparison_id,
                "conclusion": comparison_summary.conclusion,
                "output": str(args.output),
                "status": "completed",
                "summary_id": comparison_summary.summary_id,
            }
        )
        return 0
    raise AssertionError("argparse accepted an unknown command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except _UsageError as exc:
        print(f"experiment: error: {exc}", file=sys.stderr)
        return 2
    except ComparisonConfigurationError as exc:
        print(f"experiment: error: {exc}", file=sys.stderr)
        return 2
    except (ContractError, OSError, ValueError) as exc:
        print(f"experiment: failed: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
