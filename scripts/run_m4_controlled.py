#!/usr/bin/env python3
"""Run or resume the frozen serial M4 controlled experiment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cernora_reference_workflow.common import (
    ContractError,
    read_regular_file_bytes,
    validate_sha256,
)
from cernora_reference_workflow.controlled_batch_summary import (
    publish_controlled_batch_summary,
)
from cernora_reference_workflow.controlled_live_attempt import (
    ControlledHarborAttemptExecutor,
    inherited_ephemeral_environment,
)
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.controlled_runner import (
    ControlledRunStopped,
    execute_or_resume_controlled_run,
)
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority


def _task(path: Path) -> ControlledTaskAuthority:
    return ControlledTaskAuthority.from_bytes(read_regular_file_bytes(path))


def validate_task_suite(
    plan: ControlledRunPlanV2,
    tasks: tuple[ControlledTaskAuthority, ...],
) -> None:
    """Require one exact authority for every frozen M4 Case."""

    if plan.companion_version != "0.4.0":
        raise ContractError("M4 execution requires a Companion 0.4.0 RunPlan")
    indexed = {item.case.case_id: item for item in tasks}
    if len(indexed) != len(tasks) or set(indexed) != {item.case_id for item in plan.cases}:
        raise ContractError("controlled task authorities do not exactly exhaust the RunPlan")
    for case in plan.cases:
        task = indexed[case.case_id]
        if (
            task.case.case_version != case.case_version
            or task.case_sha256 != case.task_content_sha256
        ):
            raise ContractError("controlled task authority contradicts its RunPlan Case")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-plan", type=Path, required=True)
    parser.add_argument("--task-authority", action="append", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--batch-output", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        nonce = validate_sha256(args.nonce, label="execution nonce")
        plan = ControlledRunPlanV2.from_file(args.run_plan)
        tasks = tuple(_task(path) for path in args.task_authority)
        validate_task_suite(plan, tasks)
        if not args.repository_root.is_dir() or args.repository_root.is_symlink():
            raise ContractError("repository root must be one real directory")
        if not args.evaluation_root.is_dir() or args.evaluation_root.is_symlink():
            raise ContractError("evaluation root must be one existing real directory")
        executor = ControlledHarborAttemptExecutor(
            repository_root=args.repository_root,
            tasks=tasks,
            evaluation_root=args.evaluation_root,
            environment_provider=inherited_ephemeral_environment,
        )
        execution = execute_or_resume_controlled_run(
            plan,
            executor,
            store_root=args.store,
            nonce=nonce,
        )
        publish_controlled_batch_summary(execution, plan, args.batch_output)
    except ControlledRunStopped as exc:
        print(f"M4 execution safely stopped: {exc.reason}", file=sys.stderr)
        return 75
    except (ContractError, OSError, ValueError) as exc:
        print(f"M4 execution rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
