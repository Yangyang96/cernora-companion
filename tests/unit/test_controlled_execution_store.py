from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cernora_reference_workflow.common import ContractError, canonical_content_id
from cernora_reference_workflow.controlled_execution import ControlledAttemptRequest
from cernora_reference_workflow.controlled_execution_store import ControlledExecutionStore
from cernora_reference_workflow.controlled_run_plan import materialize_controlled_run_plan
from cernora_reference_workflow.controlled_runner import (
    GIBIBYTE,
    ControlledActiveSafeStop,
    ControlledRunStopped,
    execute_or_resume_controlled_run,
)
from tests.unit.test_controlled_execution import lifecycle_attempt
from tests.unit.test_controlled_run_plan import valid_m4_payload
from tests.unit.test_controlled_runner import FakeExecutor


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _first_request(store: ControlledExecutionStore) -> ControlledAttemptRequest:
    state = store.reload()
    slot = state.plan.expand_trial_slots()[0]
    spec = next(
        item
        for item in state.plan.experiment_specs
        if item.task.task_id == slot.case_id and item.configuration_id == slot.configuration_id
    )
    trial_id = canonical_content_id(
        {"execution_id": state.record.execution_id, "trial_slot_id": slot.trial_slot_id},
        excluded=frozenset(),
    )
    return ControlledAttemptRequest(
        trial_id=trial_id,
        slot=slot,
        specification=spec,
        ordinal=1,
        predecessor_attempt_id=None,
        global_deadline_monotonic=43_200,
    )


def test_store_adopts_closed_orphan_and_resume_never_reruns_it(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_m4_payload())
    nonce = digest("adopt")
    store = ControlledExecutionStore(tmp_path / "store")
    store.initialize(plan, nonce=nonce, started_unix_milliseconds=1_000_000)
    request = _first_request(store)
    active = store.begin_attempt(
        trial_id=request.trial_id,
        trial_slot_id=request.slot.trial_slot_id,
        ordinal=1,
        predecessor_attempt_id=None,
        elapsed_before_attempt_milliseconds=0,
        started_unix_milliseconds=1_000_000,
    )
    attempt = lifecycle_attempt(request)
    store.publish_attempt(active, attempt)

    state = store.reload()
    assert state.adopted_attempt_ids == (attempt.attempt_id,)
    executor = FakeExecutor()
    result = execute_or_resume_controlled_run(
        plan,
        executor,
        store_root=store.root,
        nonce=nonce,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_000.0,
        sleeper=lambda _: None,
        disk_free=lambda _: 20 * GIBIBYTE,
    )

    assert result.attempt_count == 54
    assert len(executor.requests) == 53
    assert request.trial_id not in {item.trial_id for item in executor.requests}


def test_store_blocks_ambiguous_active_attempt_instead_of_rerunning(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_m4_payload())
    store = ControlledExecutionStore(tmp_path / "store")
    store.initialize(plan, nonce=digest("ambiguous"), started_unix_milliseconds=0)
    request = _first_request(store)
    store.begin_attempt(
        trial_id=request.trial_id,
        trial_slot_id=request.slot.trial_slot_id,
        ordinal=1,
        predecessor_attempt_id=None,
        elapsed_before_attempt_milliseconds=0,
        started_unix_milliseconds=0,
    )

    with pytest.raises(ContractError, match="resume blocked"):
        store.reload()


def test_safe_stop_resume_preserves_wall_disk_and_attempt_budgets(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_m4_payload())
    nonce = digest("safe-stop")
    store_root = tmp_path / "store"
    free = iter((20 * GIBIBYTE, 20 * GIBIBYTE, 7 * GIBIBYTE))

    with pytest.raises(ControlledRunStopped, match="disk_safe_stop"):
        execute_or_resume_controlled_run(
            plan,
            FakeExecutor(),
            store_root=store_root,
            nonce=nonce,
            clock=lambda: 0.0,
            wall_clock=lambda: 1_000.0,
            sleeper=lambda _: None,
            disk_free=lambda _: next(free),
        )

    stopped = ControlledExecutionStore(store_root).reload()
    assert stopped.checkpoints[-1].status == "safe-stopped"
    assert stopped.record.attempt_budget == 108
    assert stopped.record.wall_budget_milliseconds == 43_200_000

    result = execute_or_resume_controlled_run(
        plan,
        FakeExecutor(),
        store_root=store_root,
        nonce=nonce,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_005.0,
        sleeper=lambda _: None,
        disk_free=lambda _: 20 * GIBIBYTE,
    )
    resumed = ControlledExecutionStore(store_root).reload()

    assert result.attempt_count == 54
    assert resumed.checkpoints[-1].elapsed_milliseconds == 5_000
    assert resumed.checkpoints[-1].attempt_count == 54


def test_resume_reapplies_fifteen_gibibyte_preflight_gate(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_m4_payload())
    nonce = digest("resume-preflight")
    store_root = tmp_path / "store"
    store = ControlledExecutionStore(store_root)
    store.initialize(plan, nonce=nonce, started_unix_milliseconds=1_000_000)

    with pytest.raises(ControlledRunStopped, match="disk_preflight_below_15_gib"):
        execute_or_resume_controlled_run(
            plan,
            FakeExecutor(),
            store_root=store_root,
            nonce=nonce,
            clock=lambda: 0.0,
            wall_clock=lambda: 1_001.0,
            sleeper=lambda _: None,
            disk_free=lambda _: 14 * GIBIBYTE,
        )

    state = store.reload()
    assert state.attempts == ()
    assert state.checkpoints == ()


def test_active_disk_safe_stop_is_append_only_and_resumable(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_m4_payload())
    nonce = digest("active-disk-stop")
    store_root = tmp_path / "store"

    class DiskStopExecutor(FakeExecutor):
        def __call__(self, request: ControlledAttemptRequest):  # type: ignore[no-untyped-def]
            self.requests.append(request)
            raise ControlledActiveSafeStop("disk_safe_stop_below_8_gib")

    with pytest.raises(ControlledRunStopped, match="disk_safe_stop"):
        execute_or_resume_controlled_run(
            plan,
            DiskStopExecutor(),
            store_root=store_root,
            nonce=nonce,
            clock=lambda: 0.0,
            wall_clock=lambda: 1_000.0,
            sleeper=lambda _: None,
            disk_free=lambda _: 20 * GIBIBYTE,
        )

    stopped = ControlledExecutionStore(store_root).reload()
    assert len(stopped.safe_stops) == 1
    assert stopped.checkpoints[-1].status == "safe-stopped"
    assert stopped.attempts == ()

    result = execute_or_resume_controlled_run(
        plan,
        FakeExecutor(),
        store_root=store_root,
        nonce=nonce,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_001.0,
        sleeper=lambda _: None,
        disk_free=lambda _: 20 * GIBIBYTE,
    )
    assert result.attempt_count == 54
