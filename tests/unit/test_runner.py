from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Literal

import pytest

import cernora_reference_workflow.runner as runner_module
from cernora_reference_workflow.attempt_record import publish_preterminal_attempt
from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.execution import (
    ExecutionCheckpoint,
    ExecutionTrialSlot,
    initialize_execution,
    publish_execution_manifest,
    reload_execution,
)
from cernora_reference_workflow.execution import (
    publish_checkpoint as storage_publish_checkpoint,
)
from cernora_reference_workflow.export import publish_completed_export
from cernora_reference_workflow.lifecycle import materialize_preterminal_record
from cernora_reference_workflow.run_plan import RunPlan, materialize_run_plan
from cernora_reference_workflow.runner import (
    _AttemptRequest,
    advance_repeat,
    resume_repeat,
    run_repeat,
)
from tests.unit.test_export import materialize_staging
from tests.unit.test_run_plan import valid_payload


def plan_with(
    *,
    repetitions: int = 1,
    max_attempt_count: int | None = None,
    wall_seconds: int = 86400,
) -> RunPlan:
    payload = deepcopy(valid_payload())
    assert isinstance(payload["experiment_specs"], list)
    assert isinstance(payload["cases"], list)
    assert isinstance(payload["configurations"], list)
    assert isinstance(payload["cells"], list)
    assert isinstance(payload["execution"], dict)
    payload["experiment_specs"] = payload["experiment_specs"][:1]
    payload["cases"] = payload["cases"][:1]
    payload["configurations"] = payload["configurations"][:1]
    payload["cells"] = payload["cells"][:1]
    payload["repetitions"] = repetitions
    payload["planned_trial_count"] = repetitions
    payload["worst_case_attempt_count"] = repetitions * 2
    payload["execution"]["max_attempt_count"] = max_attempt_count or repetitions * 2
    payload["execution"]["max_total_wall_time_seconds"] = wall_seconds
    return materialize_run_plan(payload)


class PreterminalExecutor:
    def __init__(self, states: tuple[str, ...] = ("runtime-pre-terminal-failure",)) -> None:
        self.states = states
        self.requests: list[_AttemptRequest] = []

    def __call__(self, request: _AttemptRequest) -> None:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.states) - 1)
        state = self.states[index]
        source_trial_id = f"source-{request.trial.slot.slot_index}-{request.active_record.ordinal}"
        terminal = materialize_preterminal_record(
            experiment_id=request.specification.experiment_id,
            source_trial_id=source_trial_id,
            state=state,  # type: ignore[arg-type]
            predecessor_attempt_id=request.active_record.predecessor_attempt_id,
        )
        publish_preterminal_attempt(
            destination=request.destination,
            experiment_id=request.specification.experiment_id,
            terminal=terminal,
            source_trial_id=source_trial_id,
        )


class CompletedExecutor:
    def __call__(self, request: _AttemptRequest) -> None:
        staging = request.destination.parent / f".completed-{request.active_record.ordinal}"
        fields = materialize_staging(staging)
        source_trial_id = f"source-{request.trial.slot.slot_index}"
        attempt_id = sha256_bytes(
            canonical_json_bytes(
                {
                    "predecessor": request.active_record.predecessor_attempt_id,
                    "source_trial_id": source_trial_id,
                }
            )
        )
        (staging / "terminal.json").write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "cernora.reference.terminal/v1",
                    "attempt_id": attempt_id,
                    "state": "completed",
                    "reason": "fixture",
                    "retry_eligible": False,
                    "predecessor_attempt_id": request.active_record.predecessor_attempt_id,
                }
            )
        )
        fields.update(
            experiment_id=request.specification.experiment_id,
            attempt_id=attempt_id,
            source_trial_id=source_trial_id,
        )
        publish_completed_export(staging, request.destination, manifest_fields=fields)


class FakeTime:
    def __init__(self, seconds: float = 100.0) -> None:
        self.seconds = seconds

    def now(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        self.seconds += seconds


def test_runner_completes_ordered_two_by_two_by_three_matrix(tmp_path: Path) -> None:
    executor = PreterminalExecutor()
    root = tmp_path / "execution"
    outcome = run_repeat(root, materialize_run_plan(valid_payload()), executor, nonce="a" * 64)

    assert outcome.status == "completed"
    assert outcome.pack_root == tmp_path / "execution.pack"
    assert outcome.pack_root.is_dir()
    assert tuple(request.trial.slot.slot_index for request in executor.requests) == tuple(
        range(1, 13)
    )
    assert len(outcome.state.trial_manifests) == 12
    assert len(outcome.state.active_attempts) == 12
    assert outcome.state.checkpoints[-1].status == "completed"
    assert (
        tuple(item.slot.repetition for item in outcome.state.trial_slots.slots)
        == (
            1,
            2,
            3,
        )
        * 4
    )


def test_advance_repeat_claims_at_most_one_external_attempt(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    plan = plan_with(repetitions=2)
    initialize_execution(root, plan, nonce="a" * 64)
    executor = PreterminalExecutor()

    first = advance_repeat(root, executor)
    assert first.status == "running"
    assert len(executor.requests) == 1

    second = advance_repeat(root, executor)
    assert second.status == "running"
    assert len(executor.requests) == 2

    completed = advance_repeat(root, executor)
    assert completed.status == "completed"
    assert len(executor.requests) == 2
    assert completed.pack_root is not None


def test_runner_evaluates_completed_export_and_publishes_available_result(
    tmp_path: Path,
) -> None:
    outcome = run_repeat(
        tmp_path / "execution",
        plan_with(),
        CompletedExecutor(),
        nonce="9" * 64,
    )

    assert outcome.status == "completed"
    assert outcome.state.trial_results[0].evaluation.status == "available"
    assert outcome.state.diagnostic is not None
    assert outcome.state.diagnostic.trials[0].result_status == "evaluated"


def test_runner_adopts_atomically_published_attempt_after_callback_crash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "execution"
    publisher = PreterminalExecutor()

    def publish_then_crash(request: _AttemptRequest) -> None:
        publisher(request)
        raise RuntimeError("simulated parent crash")

    with pytest.raises(RuntimeError, match="parent crash"):
        run_repeat(root, plan_with(), publish_then_crash, nonce="b" * 64)

    def must_not_execute(request: _AttemptRequest) -> None:
        raise AssertionError(f"adopted Attempt was rerun: {request.destination}")

    outcome = resume_repeat(root, must_not_execute)
    assert outcome.status == "completed"
    assert len(outcome.state.active_attempts) == 1


def test_runner_refuses_ambiguous_active_attempt(tmp_path: Path) -> None:
    root = tmp_path / "execution"

    def crash_before_publication(request: _AttemptRequest) -> None:
        raise RuntimeError(f"crashed before {request.destination.name}")

    with pytest.raises(RuntimeError, match="crashed before"):
        run_repeat(root, plan_with(), crash_before_publication, nonce="c" * 64)
    with pytest.raises(ContractError, match="no verifiable terminal"):
        resume_repeat(root, PreterminalExecutor())


def test_runner_enforces_attempt_budget_and_refuses_later_resume(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    executor = PreterminalExecutor(("transient-provider-pre-terminal",))
    outcome = run_repeat(
        root,
        plan_with(max_attempt_count=1),
        executor,
        nonce="d" * 64,
    )

    assert outcome.status == "budget-exhausted"
    assert outcome.state.checkpoints[-1].attempt_count == 1
    assert not outcome.state.trial_manifests
    with pytest.raises(ContractError, match="cannot be resumed"):
        resume_repeat(root, executor)


def test_runner_enforces_persisted_wall_budget_before_retry(tmp_path: Path) -> None:
    values: Iterator[float] = iter((0.0, 0.0, 2.0))
    root = tmp_path / "execution"
    outcome = run_repeat(
        root,
        plan_with(wall_seconds=1),
        PreterminalExecutor(("transient-provider-pre-terminal",)),
        nonce="e" * 64,
        clock=lambda: next(values),
    )

    assert outcome.status == "budget-exhausted"
    assert outcome.state.checkpoints[-1].elapsed_milliseconds == 2000
    assert len(outcome.state.active_attempts) == 1


def test_runner_hard_wall_budget_blocks_final_slot_completion(tmp_path: Path) -> None:
    fake = FakeTime()
    publisher = PreterminalExecutor()

    def terminal_at_deadline(request: _AttemptRequest) -> None:
        publisher(request)
        fake.seconds += 1

    root = tmp_path / "execution"
    outcome = run_repeat(
        root,
        plan_with(wall_seconds=1),
        terminal_at_deadline,
        nonce="7" * 64,
        clock=fake.now,
        wall_clock=fake.now,
        sleeper=fake.sleep,
    )

    assert outcome.status == "budget-exhausted"
    assert outcome.state.checkpoints[-1].elapsed_milliseconds == 1000
    assert outcome.state.trial_manifests == ()
    assert outcome.state.manifest is None
    assert not (tmp_path / "execution.pack").exists()


def test_runner_counts_finalization_time_before_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTime()
    original = runner_module._finalize_trial

    def slow_finalize(root: Path, trial: ExecutionTrialSlot) -> None:
        original(root, trial)
        fake.seconds += 1

    monkeypatch.setattr("cernora_reference_workflow.runner._finalize_trial", slow_finalize)
    outcome = run_repeat(
        tmp_path / "execution",
        plan_with(wall_seconds=1),
        PreterminalExecutor(),
        nonce="8" * 64,
        clock=fake.now,
        wall_clock=fake.now,
        sleeper=fake.sleep,
    )

    assert outcome.status == "budget-exhausted"
    assert len(outcome.state.trial_manifests) == 1
    assert outcome.state.manifest is None


def test_runner_rechecks_wall_between_final_running_and_completed_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTime()

    def cross_after_running(
        root: Path,
        *,
        status: Literal["running", "stopped", "budget-exhausted", "completed"] = "running",
        elapsed_milliseconds: int = 0,
    ) -> ExecutionCheckpoint:
        checkpoint = storage_publish_checkpoint(
            root, status=status, elapsed_milliseconds=elapsed_milliseconds
        )
        if status == "running" and checkpoint.completed_trials:
            fake.seconds += 1
        return checkpoint

    monkeypatch.setattr("cernora_reference_workflow.runner.publish_checkpoint", cross_after_running)
    outcome = run_repeat(
        tmp_path / "execution",
        plan_with(wall_seconds=1),
        PreterminalExecutor(),
        nonce="a" * 64,
        clock=fake.now,
        wall_clock=fake.now,
        sleeper=fake.sleep,
    )

    assert outcome.status == "budget-exhausted"
    assert [item.status for item in outcome.state.checkpoints] == [
        "running",
        "budget-exhausted",
    ]
    assert outcome.state.manifest is None
    assert outcome.pack_root is None


def test_resume_accounts_crash_downtime_without_rerunning_published_attempt(
    tmp_path: Path,
) -> None:
    fake = FakeTime()
    root = tmp_path / "execution"
    publisher = PreterminalExecutor()

    def publish_then_crash(request: _AttemptRequest) -> None:
        publisher(request)
        raise RuntimeError("crash-after-publication")

    with pytest.raises(RuntimeError, match="crash-after-publication"):
        run_repeat(
            root,
            plan_with(wall_seconds=1),
            publish_then_crash,
            nonce="3" * 64,
            clock=fake.now,
            wall_clock=fake.now,
            sleeper=fake.sleep,
        )
    fake.seconds += 0.5

    outcome = resume_repeat(
        root,
        lambda request: pytest.fail(f"published Attempt reran: {request}"),
        clock=fake.now,
        wall_clock=fake.now,
        sleeper=fake.sleep,
    )
    assert outcome.status == "completed"
    assert outcome.state.checkpoints[-1].elapsed_milliseconds >= 500


def test_resume_exhausts_wall_budget_from_crash_downtime(tmp_path: Path) -> None:
    fake = FakeTime()
    root = tmp_path / "execution"
    publisher = PreterminalExecutor()

    def publish_then_crash(request: _AttemptRequest) -> None:
        publisher(request)
        raise RuntimeError("crash-after-publication")

    with pytest.raises(RuntimeError, match="crash-after-publication"):
        run_repeat(
            root,
            plan_with(wall_seconds=1),
            publish_then_crash,
            nonce="4" * 64,
            clock=fake.now,
            wall_clock=fake.now,
            sleeper=fake.sleep,
        )
    fake.seconds += 2

    outcome = resume_repeat(
        root,
        lambda request: pytest.fail(f"published Attempt reran: {request}"),
        clock=fake.now,
        wall_clock=fake.now,
        sleeper=fake.sleep,
    )
    assert outcome.status == "budget-exhausted"
    assert outcome.state.trial_manifests == ()


def test_resume_fails_closed_when_wall_clock_moves_backward(tmp_path: Path) -> None:
    fake = FakeTime()
    root = tmp_path / "execution"
    publisher = PreterminalExecutor()

    def publish_then_crash(request: _AttemptRequest) -> None:
        publisher(request)
        raise RuntimeError("crash-after-publication")

    with pytest.raises(RuntimeError, match="crash-after-publication"):
        run_repeat(
            root,
            plan_with(),
            publish_then_crash,
            nonce="5" * 64,
            clock=fake.now,
            wall_clock=fake.now,
            sleeper=fake.sleep,
        )
    fake.seconds -= 1
    with pytest.raises(ContractError, match="wall clock moved backward"):
        resume_repeat(
            root,
            publisher,
            clock=fake.now,
            wall_clock=fake.now,
            sleeper=fake.sleep,
        )


def test_retry_waits_exact_frozen_delay_before_creating_next_active_record(
    tmp_path: Path,
) -> None:
    fake = FakeTime()
    root = tmp_path / "execution"
    executor = PreterminalExecutor(
        ("transient-provider-pre-terminal", "runtime-pre-terminal-failure")
    )
    slept: list[float] = []

    def inspect_sleep(seconds: float) -> None:
        slept.append(seconds)
        state = reload_execution(root)
        assert len(state.active_attempts) == 1
        fake.sleep(seconds)

    outcome = run_repeat(
        root,
        plan_with(wall_seconds=30),
        executor,
        nonce="6" * 64,
        clock=fake.now,
        wall_clock=fake.now,
        sleeper=inspect_sleep,
    )
    assert outcome.status == "completed"
    assert slept == [10]
    assert len(outcome.state.active_attempts) == 2


def test_retry_delay_can_exhaust_wall_budget_without_starting_attempt(
    tmp_path: Path,
) -> None:
    fake = FakeTime()
    root = tmp_path / "execution"
    outcome = run_repeat(
        root,
        plan_with(wall_seconds=5),
        PreterminalExecutor(("transient-provider-pre-terminal",)),
        nonce="0" * 64,
        clock=fake.now,
        wall_clock=fake.now,
        sleeper=fake.sleep,
    )
    assert outcome.status == "budget-exhausted"
    assert len(outcome.state.active_attempts) == 1
    assert outcome.state.checkpoints[-1].elapsed_milliseconds == 10000


def test_runner_preserves_strict_retry_chain(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    executor = PreterminalExecutor(
        ("transient-provider-pre-terminal", "runtime-pre-terminal-failure")
    )
    outcome = run_repeat(root, plan_with(), executor, nonce="f" * 64, sleeper=lambda _seconds: None)

    assert outcome.status == "completed"
    assert len(executor.requests) == 2
    first, second = outcome.state.trial_manifests[0].attempts
    assert second.predecessor_attempt_id == first.attempt_id
    assert executor.requests[1].active_record.predecessor_attempt_id == first.attempt_id


def test_runner_stops_only_between_trials_then_resumes(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    executor = PreterminalExecutor()
    outcome = run_repeat(
        root,
        plan_with(repetitions=2),
        executor,
        nonce="1" * 64,
        should_stop=lambda: len(executor.requests) >= 1,
    )

    assert outcome.status == "stopped"
    assert len(outcome.state.trial_manifests) == 1
    assert outcome.state.checkpoints[-1].status == "stopped"
    resumed = resume_repeat(root, executor)
    assert resumed.status == "completed"
    assert len(resumed.state.trial_manifests) == 2


def test_resume_finalizes_manifest_and_pack_after_completion_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "execution"
    original = publish_execution_manifest

    def crash_before_manifest(path: Path) -> None:
        raise RuntimeError(f"simulated completion crash at {path.name}")

    monkeypatch.setattr(
        "cernora_reference_workflow.runner.publish_execution_manifest", crash_before_manifest
    )
    with pytest.raises(RuntimeError, match="completion crash"):
        run_repeat(root, plan_with(), PreterminalExecutor(), nonce="2" * 64)
    monkeypatch.setattr("cernora_reference_workflow.runner.publish_execution_manifest", original)

    outcome = resume_repeat(root, PreterminalExecutor())
    assert outcome.status == "completed"
    assert outcome.state.manifest is not None
    assert outcome.pack_root is not None and outcome.pack_root.is_dir()
