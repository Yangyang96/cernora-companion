from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

from cernora_reference_workflow import controlled_study
from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    materialize_comparison_plan,
    materialize_treatment_declaration,
)
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.controlled_study import (
    AwaitingAcceptanceOutcome,
    ControlledStudyError,
    RunningOutcome,
    advance,
    materialize_heldout_reveal,
    materialize_study_intent,
    prepare,
)
from cernora_reference_workflow.execution import reload_execution
from cernora_reference_workflow.runner import _AttemptRequest
from tests.unit.test_study_execution import FakeStudyAttemptAdapter
from tests.unit.test_study_projection import study_payload_for_m4


def _comparison_plan(
    run_plan: ControlledRunPlanV2,
    split_by_case: Mapping[str, str],
) -> ComparisonPlanV1:
    statistics = run_plan.experiment_specs[0].statistical_policy
    return materialize_comparison_plan(
        {
            "schema_version": "cernora.reference.comparison-plan/v1",
            "source_run_plan_id": run_plan.run_plan_id,
            "baseline_configuration_id": "baseline",
            "candidate_configuration_id": "candidate",
            "case_splits": [
                {"case_id": item.case_id, "split_id": split_by_case[item.case_id]}
                for item in run_plan.cases
            ],
            "treatment": materialize_treatment_declaration(("prompt_instruction",)).model_dump(
                mode="json"
            ),
            "primary_outcome": {
                "metric": "reliable_success_rate",
                "scope": "split",
                "split_id": "held-out",
                "direction": "higher_is_better",
                "practical_threshold_basis_points": 1000,
            },
            "guardrails": [
                {
                    "guardrail_id": "evaluation-validity",
                    "hard": True,
                    "metric": "evaluation_validity_rate",
                    "scope": "all",
                    "split_id": None,
                    "direction": "higher_is_better",
                    "max_adverse_basis_points": 0,
                    "profile_id": None,
                    "profile_version": None,
                    "failure_code": None,
                }
            ],
            "bootstrap": statistics.bootstrap.model_dump(mode="json"),
            "pass_k": (
                None if statistics.pass_k is None else statistics.pass_k.model_dump(mode="json")
            ),
            "statistical_policy": statistics.model_dump(mode="json"),
        }
    )


def _custody_destination(tmp_path: Path, prefix: str) -> Path:
    repository = Path(__file__).resolve().parents[2]
    custody_parent = repository / ".agent" / "test-controlled-study"
    custody_parent.mkdir(parents=True, exist_ok=True)
    return custody_parent / f"{prefix}-{tmp_path.name}"


def _prepare_awaiting_acceptance(
    destination: Path,
) -> tuple[ControlledRunPlanV2, AwaitingAcceptanceOutcome, ComparisonPlanV1]:
    payload, run_plan = study_payload_for_m4()
    intent = materialize_study_intent(payload)
    split_by_case = {item.case_id: item.split for item in intent.cases}
    comparison_plan = _comparison_plan(run_plan, split_by_case)
    prepare(intent, destination)
    advance(
        destination,
        {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "request-reveal",
        },
    )
    awaiting = advance(
        destination,
        {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "bind-reveal",
            "reveal": materialize_heldout_reveal(
                {
                    "schema_version": "cernora.reference.heldout-reveal/v1",
                    "commitment_id": intent.heldout_commitment.commitment_id,
                    "manifest_sha256": intent.heldout_commitment.manifest_sha256,
                    "cases": [
                        item.model_dump(mode="json")
                        for item in intent.cases
                        if item.split == "held-out"
                    ],
                }
            ).model_dump(mode="json"),
        },
    )
    assert isinstance(awaiting, AwaitingAcceptanceOutcome)
    return run_plan, awaiting, comparison_plan


def _start_execution(destination: Path) -> tuple[ControlledRunPlanV2, RunningOutcome]:
    run_plan, awaiting, comparison_plan = _prepare_awaiting_acceptance(destination)
    outcome = advance(
        destination,
        {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "start-execution",
            "acceptance_id": awaiting.acceptance_id,
            "run_plan": run_plan.model_dump(mode="json"),
            "comparison_plan": comparison_plan.model_dump(mode="json"),
        },
    )
    assert isinstance(outcome, RunningOutcome)
    return run_plan, outcome


def test_start_execution_advance_binds_and_idempotently_initializes_repeat_runner(
    tmp_path: Path,
) -> None:
    destination = _custody_destination(tmp_path, "control")

    try:
        run_plan, awaiting, comparison_plan = _prepare_awaiting_acceptance(destination)
        prepared = prepare(materialize_study_intent(study_payload_for_m4()[0]), destination)
        directive = {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "start-execution",
            "acceptance_id": awaiting.acceptance_id,
            "run_plan": run_plan.model_dump(mode="json"),
            "comparison_plan": comparison_plan.model_dump(mode="json"),
        }

        first = advance(destination, directive)
        repeated = advance(destination, directive)
        execution = reload_execution(destination / "execution")

        assert isinstance(first, RunningOutcome)
        assert repeated == first
        assert first.status == "running"
        assert first.study_id == prepared.study_id
        assert first.execution_id == execution.record.execution_id
        assert execution.run_plan == run_plan
        assert len(tuple((destination / "ledger").glob("*.json"))) == 4
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_start_execution_repairs_crash_after_ledger_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = _custody_destination(tmp_path, "crash")

    try:
        run_plan, awaiting, comparison_plan = _prepare_awaiting_acceptance(destination)
        directive = {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "start-execution",
            "acceptance_id": awaiting.acceptance_id,
            "run_plan": run_plan.model_dump(mode="json"),
            "comparison_plan": comparison_plan.model_dump(mode="json"),
        }

        def fail_initialization(*args: object, **kwargs: object) -> None:
            raise OSError("simulated crash boundary")

        monkeypatch.setattr(controlled_study, "initialize_execution", fail_initialization)
        with pytest.raises(ControlledStudyError, match="advance:corrupt-ledger"):
            advance(destination, directive)

        assert len(tuple((destination / "ledger").glob("*.json"))) == 4
        assert not (destination / "execution").exists()

        monkeypatch.undo()
        recovered = advance(destination, directive)
        execution = reload_execution(destination / "execution")

        assert isinstance(recovered, RunningOutcome)
        assert recovered.execution_id == execution.record.execution_id
        assert execution.run_plan == run_plan
        assert len(tuple((destination / "ledger").glob("*.json"))) == 4
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_start_execution_rejects_non_heldout_primary_authority(tmp_path: Path) -> None:
    destination = _custody_destination(tmp_path, "scope")

    try:
        run_plan, awaiting, comparison_plan = _prepare_awaiting_acceptance(destination)
        comparison_payload = comparison_plan.model_dump(
            mode="json", exclude={"comparison_plan_id", "comparison_plan_sha256"}
        )
        comparison_payload["primary_outcome"] = {
            **comparison_plan.primary_outcome.model_dump(mode="json", exclude={"split_id"}),
            "scope": "all",
        }
        all_scope = materialize_comparison_plan(comparison_payload)

        with pytest.raises(ControlledStudyError, match="advance:authority-mismatch"):
            advance(
                destination,
                {
                    "schema_version": "cernora.reference.advance-directive/v1",
                    "action": "start-execution",
                    "acceptance_id": awaiting.acceptance_id,
                    "run_plan": run_plan.model_dump(mode="json"),
                    "comparison_plan": all_scope.model_dump(mode="json"),
                },
            )

        assert len(tuple((destination / "ledger").glob("*.json"))) == 3
        assert not (destination / "execution").exists()
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_step_execution_is_idempotent_and_claims_only_one_external_attempt(
    tmp_path: Path,
) -> None:
    destination = _custody_destination(tmp_path, "step")

    try:
        _, running = _start_execution(destination)
        directive = {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "step-execution",
            "expected_state_id": running.state_id,
        }
        adapter = FakeStudyAttemptAdapter()

        first = advance(destination, directive, executor=adapter)

        def must_not_execute(request: _AttemptRequest) -> None:
            raise AssertionError(f"idempotent Study step reran: {request.destination}")

        repeated = advance(destination, directive, executor=must_not_execute)
        execution = reload_execution(destination / "execution")

        assert isinstance(first, RunningOutcome)
        assert repeated == first
        assert len(adapter.requests) == 1
        assert len(execution.active_attempts) == 1
        assert len(tuple((destination / "ledger").glob("*.json"))) == 6
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_step_execution_recovers_published_attempt_without_claiming_the_next(
    tmp_path: Path,
) -> None:
    destination = _custody_destination(tmp_path, "step-crash")

    try:
        _, running = _start_execution(destination)
        directive = {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "step-execution",
            "expected_state_id": running.state_id,
        }
        publisher = FakeStudyAttemptAdapter()

        def publish_then_crash(request: _AttemptRequest) -> None:
            publisher(request)
            raise RuntimeError("simulated crash after Attempt publication")

        with pytest.raises(RuntimeError, match="after Attempt publication"):
            advance(destination, directive, executor=publish_then_crash)

        assert len(tuple((destination / "ledger").glob("*.json"))) == 5
        assert len(reload_execution(destination / "execution").active_attempts) == 1

        def must_not_execute(request: _AttemptRequest) -> None:
            raise AssertionError(f"recovery claimed another Attempt: {request.destination}")

        recovered = advance(destination, directive, executor=must_not_execute)
        execution = reload_execution(destination / "execution")

        assert isinstance(recovered, RunningOutcome)
        assert len(publisher.requests) == 1
        assert len(execution.active_attempts) == 1
        assert len(execution.trial_manifests) == 1
        assert len(tuple((destination / "ledger").glob("*.json"))) == 6
    finally:
        shutil.rmtree(destination, ignore_errors=True)
