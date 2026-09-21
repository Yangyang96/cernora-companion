"""Command-line surface for the Priority 4 Repeat Runner and batch normalizer."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cernora_reference_workflow.batch_summary import summarize_execution_pack
from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.comparison_input import (
    ComparisonConfigurationError,
    compare_batch_summary,
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
    from cernora_reference_workflow.skill_capture.__main__ import configure

    configure(
        commands.add_parser("skill", help="inspect, capture or evaluate one pi Skill attempt")
    )
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
    if args.command == "skill":
        from cernora_reference_workflow.skill_capture.__main__ import dispatch

        _emit(dispatch(args))
        return 0
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
