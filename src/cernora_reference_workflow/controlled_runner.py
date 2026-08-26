"""Sequential, budgeted Runner for controlled RunPlan v2."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from cernora_reference_workflow.common import canonical_content_id
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptExecutor,
    ControlledAttemptRequest,
    ControlledExecutionResult,
    ControlledTrialExecution,
)
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2

GIBIBYTE = 1024**3
PREFLIGHT_FREE_BYTES = 15 * GIBIBYTE
SAFE_STOP_FREE_BYTES = 8 * GIBIBYTE
HARD_WALL_SECONDS = 43_200
MAX_ATTEMPT_COUNT = 108

Clock = Callable[[], float]
Sleeper = Callable[[float], None]
DiskProbe = Callable[[Path], int]


class ControlledRunStopped(RuntimeError):
    """Fail-closed stop that never masquerades as a complete Batch."""

    def __init__(self, reason: str, *, completed_trials: int, attempt_count: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.completed_trials = completed_trials
        self.attempt_count = attempt_count


def _disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


def execute_controlled_run(
    plan: ControlledRunPlanV2,
    executor: ControlledAttemptExecutor,
    *,
    workspace: Path,
    nonce: str,
    clock: Clock = time.monotonic,
    sleeper: Sleeper = time.sleep,
    disk_free: DiskProbe = _disk_free,
) -> ControlledExecutionResult:
    """Execute exactly two configurations and three repetitions, serially."""

    if plan.repetitions != 3:
        raise ValueError("M4 controlled execution requires exactly three repetitions")
    if plan.execution.concurrency != 1:
        raise ValueError("M4 controlled execution requires concurrency=1")
    if plan.execution.max_total_wall_time_seconds != HARD_WALL_SECONDS:
        raise ValueError("M4 controlled execution requires the frozen 12-hour wall budget")
    if plan.execution.max_attempt_count > MAX_ATTEMPT_COUNT:
        raise ValueError("M4 controlled execution exceeds the frozen 108-Attempt maximum")
    if not executor.enforces_hard_deadline:
        raise ValueError("Attempt executor must enforce the active global deadline")
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("controlled execution workspace must be one real directory")
    if disk_free(workspace) < PREFLIGHT_FREE_BYTES:
        raise ControlledRunStopped(
            "disk_preflight_below_15_gib", completed_trials=0, attempt_count=0
        )

    started = clock()
    deadline = started + HARD_WALL_SECONDS
    execution_id = canonical_content_id(
        {"nonce": nonce, "run_plan_id": plan.run_plan_id}, excluded=frozenset()
    )
    specs = {(item.task.task_id, item.configuration_id): item for item in plan.experiment_specs}
    completed: list[ControlledTrialExecution] = []
    attempt_count = 0

    for slot in plan.expand_trial_slots():
        trial_id = canonical_content_id(
            {"execution_id": execution_id, "trial_slot_id": slot.trial_slot_id},
            excluded=frozenset(),
        )
        attempts: list[ControlledAttempt] = []
        while True:
            if clock() >= deadline:
                raise ControlledRunStopped(
                    "hard_wall_deadline_elapsed",
                    completed_trials=len(completed),
                    attempt_count=attempt_count,
                )
            if disk_free(workspace) < SAFE_STOP_FREE_BYTES:
                raise ControlledRunStopped(
                    "disk_safe_stop_below_8_gib",
                    completed_trials=len(completed),
                    attempt_count=attempt_count,
                )
            if attempt_count >= plan.execution.max_attempt_count:
                raise ControlledRunStopped(
                    "attempt_budget_exhausted",
                    completed_trials=len(completed),
                    attempt_count=attempt_count,
                )
            specification = specs[(slot.case_id, slot.configuration_id)]
            request = ControlledAttemptRequest(
                trial_id=trial_id,
                slot=slot,
                specification=specification,
                ordinal=len(attempts) + 1,
                predecessor_attempt_id=attempts[-1].attempt_id if attempts else None,
                global_deadline_monotonic=deadline,
            )
            attempt = executor(request)
            attempt_count += 1
            if clock() > deadline:
                raise ControlledRunStopped(
                    "attempt_returned_after_hard_deadline",
                    completed_trials=len(completed),
                    attempt_count=attempt_count,
                )
            if (
                attempt.trial_id != trial_id
                or attempt.ordinal != request.ordinal
                or attempt.predecessor_attempt_id != request.predecessor_attempt_id
            ):
                raise ValueError("Attempt executor returned an object for another request")
            attempt.verify_authority(specification)
            attempts.append(attempt)
            if not attempt.retry_eligible:
                break
            if len(attempts) > specification.retry.max_retries:
                raise ValueError("Attempt executor exceeded the frozen retry scope")
            sleeper(float(specification.retry.delay_seconds))
        completed.append(
            ControlledTrialExecution(
                schema_version="cernora.reference.controlled-trial-execution/v1",
                trial_id=trial_id,
                slot=slot,
                attempts=tuple(attempts),
                selected_attempt_id=attempts[-1].attempt_id,
            )
        )

    result = ControlledExecutionResult(
        schema_version="cernora.reference.controlled-execution-result/v1",
        execution_id=execution_id,
        run_plan_id=plan.run_plan_id,
        nonce=nonce,
        status="completed",
        budget_status="within_budget",
        trials=tuple(completed),
        attempt_count=attempt_count,
    )
    result.verify_plan(plan)
    return result


__all__ = [
    "GIBIBYTE",
    "HARD_WALL_SECONDS",
    "MAX_ATTEMPT_COUNT",
    "PREFLIGHT_FREE_BYTES",
    "SAFE_STOP_FREE_BYTES",
    "ControlledRunStopped",
    "execute_controlled_run",
]
