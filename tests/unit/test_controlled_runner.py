from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptRequest,
)
from cernora_reference_workflow.controlled_run_plan import materialize_controlled_run_plan
from cernora_reference_workflow.controlled_runner import (
    GIBIBYTE,
    ControlledRunStopped,
    execute_controlled_run,
)
from tests.unit.test_controlled_execution import lifecycle_attempt
from tests.unit.test_controlled_run_plan import valid_payload


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FakeExecutor:
    def __init__(self, *, retry_first: bool = False, clock: list[float] | None = None) -> None:
        self.retry_first = retry_first
        self.clock = clock
        self.requests: list[ControlledAttemptRequest] = []

    @property
    def enforces_hard_deadline(self) -> bool:
        return True

    def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
        self.requests.append(request)
        if self.clock is not None:
            self.clock[0] = request.global_deadline_monotonic + 1
        retry = self.retry_first and request.slot.slot_index == 1 and request.ordinal == 1
        return lifecycle_attempt(request, retry_eligible=retry)


class NoDeadlineExecutor(FakeExecutor):
    @property
    def enforces_hard_deadline(self) -> bool:
        return False


def test_runner_executes_exact_serial_matrix_and_keeps_retry_inside_trial(
    tmp_path: Path,
) -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    executor = FakeExecutor(retry_first=True)
    sleeps: list[float] = []

    result = execute_controlled_run(
        plan,
        executor,
        workspace=tmp_path,
        nonce=digest("nonce"),
        clock=lambda: 0.0,
        sleeper=sleeps.append,
        disk_free=lambda _: 20 * GIBIBYTE,
    )

    assert len(result.trials) == plan.planned_trial_count == 12
    assert result.attempt_count == 13
    assert len(result.trials[0].attempts) == 2
    assert all(len(item.attempts) == 1 for item in result.trials[1:])
    assert sleeps == [10.0]
    assert tuple(item.slot.slot_index for item in result.trials) == tuple(range(1, 13))


@pytest.mark.parametrize(
    ("free_bytes", "reason"),
    ((14 * GIBIBYTE, "disk_preflight_below_15_gib"), (7 * GIBIBYTE, "disk_safe_stop")),
)
def test_runner_disk_gates_fail_closed(tmp_path: Path, free_bytes: int, reason: str) -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    values = [20 * GIBIBYTE, free_bytes] if reason == "disk_safe_stop" else [free_bytes]

    with pytest.raises(ControlledRunStopped, match=reason):
        execute_controlled_run(
            plan,
            FakeExecutor(),
            workspace=tmp_path,
            nonce=digest("nonce"),
            clock=lambda: 0.0,
            sleeper=lambda _: None,
            disk_free=lambda _: values.pop(0) if values else free_bytes,
        )


def test_runner_rejects_attempt_return_after_active_hard_deadline(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    now = [0.0]
    executor = FakeExecutor(clock=now)

    with pytest.raises(ControlledRunStopped, match="after_hard_deadline"):
        execute_controlled_run(
            plan,
            executor,
            workspace=tmp_path,
            nonce=digest("nonce"),
            clock=lambda: now[0],
            sleeper=lambda _: None,
            disk_free=lambda _: 20 * GIBIBYTE,
        )


def test_runner_rejects_executor_without_active_deadline_guarantee(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    with pytest.raises(ValueError, match="must enforce"):
        execute_controlled_run(
            plan,
            NoDeadlineExecutor(),
            workspace=tmp_path,
            nonce=digest("nonce"),
            disk_free=lambda _: 20 * GIBIBYTE,
        )
