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
from cernora_reference_workflow.controlled_execution_store import ControlledExecutionStore
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2

GIBIBYTE = 1024**3
PREFLIGHT_FREE_BYTES = 15 * GIBIBYTE
SAFE_STOP_FREE_BYTES = 8 * GIBIBYTE
HARD_WALL_SECONDS = 43_200
MAX_ATTEMPT_COUNT = 108

Clock = Callable[[], float]
WallClock = Callable[[], float]
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
            delay = float(specification.retry.delay_seconds)
            if clock() + delay >= deadline:
                raise ControlledRunStopped(
                    "retry_delay_would_overrun_hard_deadline",
                    completed_trials=len(completed),
                    attempt_count=attempt_count,
                )
            sleeper(delay)
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


def execute_or_resume_controlled_run(
    plan: ControlledRunPlanV2,
    executor: ControlledAttemptExecutor,
    *,
    store_root: Path,
    nonce: str,
    clock: Clock = time.monotonic,
    wall_clock: WallClock = time.time,
    sleeper: Sleeper = time.sleep,
    disk_free: DiskProbe = _disk_free,
) -> ControlledExecutionResult:
    """Persist every boundary and resume without resetting any frozen budget."""

    if plan.companion_version != "0.4.0":
        raise ValueError("stored M4 Runner requires a Companion 0.4.0 RunPlan")
    if not executor.enforces_hard_deadline:
        raise ValueError("Attempt executor must enforce the active global deadline")
    store = ControlledExecutionStore(store_root)
    now_unix = int(wall_clock() * 1000)
    if not store_root.exists() and not store_root.is_symlink():
        if disk_free(store_root.parent) < PREFLIGHT_FREE_BYTES:
            raise ControlledRunStopped(
                "disk_preflight_below_15_gib", completed_trials=0, attempt_count=0
            )
        state = store.initialize(
            plan,
            nonce=nonce,
            started_unix_milliseconds=now_unix,
        )
    else:
        state = store.reload()
        if state.plan != plan or state.record.nonce != nonce:
            raise ValueError("resume store does not bind the supplied RunPlan and nonce")
    if state.checkpoints and state.checkpoints[-1].status == "completed":
        return store.completed_result()

    def elapsed() -> int:
        observed = int(wall_clock() * 1000)
        if state.checkpoints:
            checkpoint = state.checkpoints[-1]
            if observed < checkpoint.observed_unix_milliseconds:
                raise ValueError("wall clock moved backward since checkpoint")
            return (
                checkpoint.elapsed_milliseconds + observed - checkpoint.observed_unix_milliseconds
            )
        if observed < state.record.started_unix_milliseconds:
            raise ValueError("wall clock moved backward since execution start")
        return observed - state.record.started_unix_milliseconds

    attempts_by_trial: dict[str, list[ControlledAttempt]] = {}
    for attempt in state.attempts:
        attempts_by_trial.setdefault(attempt.trial_id, []).append(attempt)
    completed_ids: list[str] = []
    for trial_id, attempts in attempts_by_trial.items():
        attempts.sort(key=lambda item: item.ordinal)
        if not attempts[-1].retry_eligible or len(attempts) == 2:
            completed_ids.append(trial_id)
    completed_ids.sort()
    if state.adopted_attempt_ids:
        store.checkpoint(
            status="running",
            elapsed_milliseconds=elapsed(),
            completed_trial_ids=tuple(completed_ids),
            observed_unix_milliseconds=int(wall_clock() * 1000),
        )
        state = store.reload()

    specs = {(item.task.task_id, item.configuration_id): item for item in plan.experiment_specs}
    for slot in plan.expand_trial_slots():
        trial_id = canonical_content_id(
            {"execution_id": state.record.execution_id, "trial_slot_id": slot.trial_slot_id},
            excluded=frozenset(),
        )
        attempts = attempts_by_trial.setdefault(trial_id, [])
        if trial_id in completed_ids:
            continue
        while True:
            elapsed_ms = elapsed()
            remaining_ms = state.record.wall_budget_milliseconds - elapsed_ms
            if remaining_ms <= 0:
                store.checkpoint(
                    status="safe-stopped",
                    elapsed_milliseconds=elapsed_ms,
                    completed_trial_ids=tuple(sorted(completed_ids)),
                    observed_unix_milliseconds=int(wall_clock() * 1000),
                )
                raise ControlledRunStopped(
                    "hard_wall_deadline_elapsed",
                    completed_trials=len(completed_ids),
                    attempt_count=len(state.attempts),
                )
            if disk_free(store_root) < SAFE_STOP_FREE_BYTES:
                store.checkpoint(
                    status="safe-stopped",
                    elapsed_milliseconds=elapsed_ms,
                    completed_trial_ids=tuple(sorted(completed_ids)),
                    observed_unix_milliseconds=int(wall_clock() * 1000),
                )
                raise ControlledRunStopped(
                    "disk_safe_stop_below_8_gib",
                    completed_trials=len(completed_ids),
                    attempt_count=len(state.attempts),
                )
            if len(state.attempts) >= state.record.attempt_budget:
                raise ControlledRunStopped(
                    "attempt_budget_exhausted",
                    completed_trials=len(completed_ids),
                    attempt_count=len(state.attempts),
                )
            spec = specs[(slot.case_id, slot.configuration_id)]
            ordinal = len(attempts) + 1
            predecessor = attempts[-1].attempt_id if attempts else None
            active = store.begin_attempt(
                trial_id=trial_id,
                trial_slot_id=slot.trial_slot_id,
                ordinal=ordinal,
                predecessor_attempt_id=predecessor,
                elapsed_before_attempt_milliseconds=elapsed_ms,
                started_unix_milliseconds=int(wall_clock() * 1000),
            )
            deadline = clock() + remaining_ms / 1000
            request = ControlledAttemptRequest(
                trial_id=trial_id,
                slot=slot,
                specification=spec,
                ordinal=ordinal,
                predecessor_attempt_id=predecessor,
                global_deadline_monotonic=deadline,
            )
            attempt = executor(request)
            if clock() > deadline:
                raise ControlledRunStopped(
                    "attempt_returned_after_hard_deadline",
                    completed_trials=len(completed_ids),
                    attempt_count=len(state.attempts) + 1,
                )
            attempt.verify_authority(spec)
            store.publish_attempt(active, attempt)
            attempts.append(attempt)
            state = store.reload()
            if not attempt.retry_eligible:
                break
            if ordinal > spec.retry.max_retries:
                raise ValueError("Attempt executor exceeded the frozen retry scope")
            delay = spec.retry.delay_seconds
            if delay * 1000 >= state.record.wall_budget_milliseconds - elapsed():
                store.checkpoint(
                    status="safe-stopped",
                    elapsed_milliseconds=elapsed(),
                    completed_trial_ids=tuple(sorted(completed_ids)),
                    observed_unix_milliseconds=int(wall_clock() * 1000),
                )
                raise ControlledRunStopped(
                    "retry_delay_would_overrun_hard_deadline",
                    completed_trials=len(completed_ids),
                    attempt_count=len(state.attempts),
                )
            sleeper(float(delay))
        completed_ids.append(trial_id)
        completed_ids.sort()
        store.checkpoint(
            status="running",
            elapsed_milliseconds=elapsed(),
            completed_trial_ids=tuple(completed_ids),
            observed_unix_milliseconds=int(wall_clock() * 1000),
        )
        state = store.reload()
    store.checkpoint(
        status="completed",
        elapsed_milliseconds=elapsed(),
        completed_trial_ids=tuple(sorted(completed_ids)),
        observed_unix_milliseconds=int(wall_clock() * 1000),
    )
    return store.completed_result()


__all__ = [
    "GIBIBYTE",
    "HARD_WALL_SECONDS",
    "MAX_ATTEMPT_COUNT",
    "PREFLIGHT_FREE_BYTES",
    "SAFE_STOP_FREE_BYTES",
    "ControlledRunStopped",
    "execute_controlled_run",
    "execute_or_resume_controlled_run",
]
