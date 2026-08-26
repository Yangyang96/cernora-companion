from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.attempt_record import publish_preterminal_attempt
from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    load_json_file,
    sha256_file,
)
from cernora_reference_workflow.execution import (
    initialize_execution,
    publish_checkpoint,
    publish_execution_manifest,
    publish_trial_manifest,
    publish_trial_result,
    reload_execution,
    start_attempt,
)
from cernora_reference_workflow.lifecycle import (
    TerminalRecord,
    materialize_preterminal_record,
)
from cernora_reference_workflow.report_builder import build_unavailable_run_report
from cernora_reference_workflow.run_plan import RunPlan, materialize_run_plan
from tests.unit.test_run_plan import valid_payload


def small_plan(*, repetitions: int = 2) -> RunPlan:
    payload = valid_payload()
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
    payload["execution"]["max_attempt_count"] = repetitions * 2
    return materialize_run_plan(payload)


def publish_terminal(
    root: Path,
    *,
    trial_id: str,
    experiment_id: str,
    ordinal: int,
    state: str = "runtime-pre-terminal-failure",
    predecessor: str | None = None,
    source_trial_id: str | None = None,
) -> str:
    source_id = source_trial_id or trial_id
    terminal = materialize_preterminal_record(
        experiment_id=experiment_id,
        source_trial_id=source_id,
        state=state,  # type: ignore[arg-type]
        predecessor_attempt_id=predecessor,
    )
    publish_preterminal_attempt(
        destination=root / "attempts" / trial_id / f"{ordinal:04d}",
        experiment_id=experiment_id,
        terminal=terminal,
        source_trial_id=source_id,
    )
    return terminal.attempt_id


def publish_unavailable_result(root: Path, trial_id: str) -> None:
    state = reload_execution(root)
    slot = next(item for item in state.trial_slots.slots if item.trial_id == trial_id)
    spec = next(
        item
        for item in state.run_plan.experiment_specs
        if item.experiment_id == slot.slot.experiment_id
    )
    attempts: list[tuple[TerminalRecord, str, str | None]] = []
    for active in (item for item in state.active_attempts if item.trial_id == trial_id):
        attempt_root = root / "attempts" / trial_id / f"{active.ordinal:04d}"
        terminal_payload = load_json_file(attempt_root / "terminal.json")
        manifest_payload = load_json_file(attempt_root / "manifest.json")
        assert isinstance(terminal_payload, dict)
        assert isinstance(manifest_payload, dict)
        attempts.append(
            (
                TerminalRecord.model_validate(terminal_payload),
                str(manifest_payload["source_trial_id"]),
                sha256_file(attempt_root / "manifest.json"),
            )
        )
    report = build_unavailable_run_report(spec=spec, attempts=tuple(attempts))
    publish_trial_result(root, trial_id, report=report)


def test_initialization_freezes_plan_slots_and_execution_bound_trial_ids(tmp_path: Path) -> None:
    plan = small_plan()
    state = initialize_execution(tmp_path / "execution", plan, nonce="1" * 64)

    assert state.record.run_plan_id == plan.run_plan_id
    assert len(state.trial_slots.slots) == 2
    assert len({item.trial_id for item in state.trial_slots.slots}) == 2
    assert all(item.slot.run_plan_id == plan.run_plan_id for item in state.trial_slots.slots)
    with pytest.raises(ContractError, match="destination"):
        initialize_execution(tmp_path / "execution", plan, nonce="2" * 64)


def test_ambiguous_active_attempt_blocks_reload_and_duplicate_start(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    state = initialize_execution(root, small_plan(), nonce="1" * 64)
    trial = state.trial_slots.slots[0]
    active = start_attempt(
        root,
        trial.trial_id,
        elapsed_before_attempt_milliseconds=125,
        started_unix_milliseconds=1_000,
    )

    assert active.elapsed_before_attempt_milliseconds == 125
    assert active.started_unix_milliseconds == 1_000
    assert active.wall_deadline_unix_milliseconds == 86_400_875

    with pytest.raises(ContractError, match="no verifiable terminal"):
        reload_execution(root)
    with pytest.raises(ContractError, match="active Attempt"):
        start_attempt(root, trial.trial_id)


def test_sequential_execution_cannot_skip_an_ambiguous_or_unfinalized_slot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "execution"
    state = initialize_execution(root, small_plan(), nonce="1" * 64)
    first, second = state.trial_slots.slots
    with pytest.raises(ContractError, match="sequential Trial-slot order"):
        start_attempt(root, second.trial_id)
    start_attempt(root, first.trial_id)
    with pytest.raises(ContractError, match="active Attempt"):
        start_attempt(root, second.trial_id)
    publish_terminal(
        root,
        trial_id=first.trial_id,
        experiment_id=first.slot.experiment_id,
        ordinal=1,
    )
    with pytest.raises(ContractError, match="sequential Trial-slot order"):
        start_attempt(root, second.trial_id)


def test_retry_chain_uses_terminal_record_and_trial_is_append_only(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    state = initialize_execution(root, small_plan(), nonce="1" * 64)
    trial = state.trial_slots.slots[0]
    start_attempt(root, trial.trial_id)
    first_id = publish_terminal(
        root,
        trial_id=trial.trial_id,
        experiment_id=trial.slot.experiment_id,
        ordinal=1,
        state="transient-provider-pre-terminal",
    )
    with pytest.raises(ContractError, match="frozen retry"):
        publish_unavailable_result(root, trial.trial_id)

    start_attempt(root, trial.trial_id, predecessor_attempt_id=first_id)
    publish_terminal(
        root,
        trial_id=trial.trial_id,
        experiment_id=trial.slot.experiment_id,
        ordinal=2,
        predecessor=first_id,
    )
    publish_unavailable_result(root, trial.trial_id)
    manifest = publish_trial_manifest(root, trial.trial_id)
    assert next(item.attempt_id for item in manifest.attempts) == first_id
    assert manifest.selected_attempt_id == manifest.attempts[-1].attempt_id
    with pytest.raises(ContractError, match="completed Trial"):
        start_attempt(root, trial.trial_id)
    with pytest.raises(ContractError, match="already exists"):
        publish_trial_manifest(root, trial.trial_id)


def test_trial_binds_p3_source_trial_id_without_rewriting_it(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    state = initialize_execution(root, small_plan(), nonce="1" * 64)
    trial = state.trial_slots.slots[0]
    start_attempt(root, trial.trial_id)
    publish_terminal(
        root,
        trial_id=trial.trial_id,
        experiment_id=trial.slot.experiment_id,
        ordinal=1,
        source_trial_id="harbor-trial-1",
    )

    publish_unavailable_result(root, trial.trial_id)
    manifest = publish_trial_manifest(root, trial.trial_id)
    assert manifest.attempts[0].source_trial_id == "harbor-trial-1"
    assert reload_execution(root).trial_manifests == (manifest,)


def test_reload_adopts_verified_terminal_then_hash_chains_checkpoints(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    state = initialize_execution(root, small_plan(), nonce="1" * 64)
    publish_checkpoint(root)
    trial = state.trial_slots.slots[0]
    start_attempt(root, trial.trial_id)
    publish_terminal(
        root,
        trial_id=trial.trial_id,
        experiment_id=trial.slot.experiment_id,
        ordinal=1,
    )
    publish_unavailable_result(root, trial.trial_id)
    publish_trial_manifest(root, trial.trial_id)

    adopted = reload_execution(root)
    assert adopted.adopted_trial_ids == (trial.trial_id,)
    second = publish_checkpoint(root)
    assert second.sequence == 2
    assert second.previous_checkpoint_sha256 is not None
    assert reload_execution(root).adopted_trial_ids == ()


def test_complete_manifest_requires_every_slot_once_and_verifies_corruption(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    state = initialize_execution(root, small_plan(), nonce="1" * 64)
    first = state.trial_slots.slots[0]
    start_attempt(root, first.trial_id)
    publish_terminal(
        root,
        trial_id=first.trial_id,
        experiment_id=first.slot.experiment_id,
        ordinal=1,
    )
    publish_unavailable_result(root, first.trial_id)
    publish_trial_manifest(root, first.trial_id)
    publish_checkpoint(root)
    with pytest.raises(ContractError, match="every planned Trial"):
        publish_execution_manifest(root)

    second = state.trial_slots.slots[1]
    start_attempt(root, second.trial_id)
    publish_terminal(
        root,
        trial_id=second.trial_id,
        experiment_id=second.slot.experiment_id,
        ordinal=1,
    )
    publish_unavailable_result(root, second.trial_id)
    publish_trial_manifest(root, second.trial_id)
    publish_checkpoint(root, status="completed")
    manifest = publish_execution_manifest(root)
    assert len(manifest.trials) == 2
    assert reload_execution(root).manifest == manifest

    checkpoint = root / "checkpoints/00000002.json"
    checkpoint.write_bytes(canonical_json_bytes({"corrupt": True}))
    with pytest.raises((ContractError, ValueError)):
        reload_execution(root)


def test_checkpoint_status_budget_and_completion_transitions_are_strict(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    state = initialize_execution(root, small_plan(), nonce="1" * 64)
    with pytest.raises(ContractError, match="every planned Trial"):
        publish_checkpoint(root, status="completed")
    with pytest.raises(ContractError, match="frozen budget"):
        publish_checkpoint(root, status="budget-exhausted")

    stopped = publish_checkpoint(root, status="stopped", elapsed_milliseconds=5)
    assert stopped.attempt_count == 0
    resumed = publish_checkpoint(root, status="running", elapsed_milliseconds=6)
    assert resumed.sequence == 2
    exhausted = publish_checkpoint(
        root,
        status="budget-exhausted",
        elapsed_milliseconds=state.run_plan.execution.max_total_wall_time_seconds * 1000,
    )
    assert exhausted.status == "budget-exhausted"
    with pytest.raises(ContractError, match="terminal checkpoint"):
        publish_checkpoint(root, elapsed_milliseconds=exhausted.elapsed_milliseconds)


def test_completed_checkpoint_must_remain_below_wall_budget(tmp_path: Path) -> None:
    root = tmp_path / "execution"
    state = initialize_execution(root, small_plan(repetitions=1), nonce="8" * 64)
    trial = state.trial_slots.slots[0]
    start_attempt(root, trial.trial_id)
    publish_terminal(
        root,
        trial_id=trial.trial_id,
        experiment_id=trial.slot.experiment_id,
        ordinal=1,
    )
    publish_unavailable_result(root, trial.trial_id)
    publish_trial_manifest(root, trial.trial_id)
    wall_budget = state.run_plan.execution.max_total_wall_time_seconds * 1000

    with pytest.raises(ContractError, match="cannot reach the frozen wall budget"):
        publish_checkpoint(root, status="completed", elapsed_milliseconds=wall_budget)

    publish_checkpoint(root, status="completed", elapsed_milliseconds=wall_budget - 1)
    checkpoint_path = root / "checkpoints/00000001.json"
    payload = load_json_file(checkpoint_path)
    assert isinstance(payload, dict)
    payload["elapsed_milliseconds"] = wall_budget
    payload["checkpoint_id"] = canonical_content_id(payload, excluded=frozenset({"checkpoint_id"}))
    checkpoint_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ContractError, match="cannot reach the frozen wall budget"):
        reload_execution(root)


def test_checkpoint_rejects_nonmonotonic_elapsed_and_attempt_count_corruption(
    tmp_path: Path,
) -> None:
    root = tmp_path / "execution"
    initialize_execution(root, small_plan(), nonce="1" * 64)
    publish_checkpoint(root, elapsed_milliseconds=10)
    with pytest.raises(ContractError, match="not monotonic"):
        publish_checkpoint(root, elapsed_milliseconds=9)

    checkpoint = root / "checkpoints/00000001.json"
    payload = checkpoint.read_bytes()
    checkpoint.write_bytes(payload.replace(b'"attempt_count":0', b'"attempt_count":1'))
    with pytest.raises((ContractError, ValueError), match="Attempt count|identity"):
        reload_execution(root)
    checkpoint.write_bytes(payload)


@pytest.mark.parametrize("mutation", ("unknown", "missing", "digest", "slot"))
def test_reload_fails_closed_for_unknown_missing_digest_and_slot_corruption(
    tmp_path: Path, mutation: str
) -> None:
    root = tmp_path / "execution"
    initialize_execution(root, small_plan(), nonce="1" * 64)
    if mutation == "unknown":
        (root / "unknown.json").write_text("{}", encoding="utf-8")
    elif mutation == "missing":
        (root / "trial-slots.json").unlink()
    elif mutation == "digest":
        (root / "run-plan.json").write_bytes(b"{}")
    else:
        payload = (root / "trial-slots.json").read_bytes()
        (root / "trial-slots.json").write_bytes(
            payload.replace(b'"slot_index":1', b'"slot_index":9')
        )
    with pytest.raises((ContractError, ValueError)):
        reload_execution(root)
