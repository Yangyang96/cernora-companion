from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import pytest
from cernora import reload_batch_summary_package, reload_comparison_package

from cernora_reference_workflow import controlled_study
from cernora_reference_workflow.candidate_development import freeze_candidate_development
from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    materialize_comparison_plan,
    materialize_treatment_declaration,
)
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.controlled_study import (
    AwaitingAcceptanceOutcome,
    CompletedOutcome,
    ControlledStudyError,
    PausedOutcome,
    RunningOutcome,
    StudyArtifactManifest,
    advance,
    materialize_heldout_commitment,
    materialize_heldout_reveal,
    materialize_implementation_lock,
    materialize_study_analysis_policy,
    materialize_study_intent,
    prepare,
    rebuild,
)
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.execution import reload_execution
from cernora_reference_workflow.runner import _AttemptRequest
from cernora_reference_workflow.study_projection import (
    case_authority_sha256,
    configuration_authority_sha256,
)
from tests.support.study_cases import _all_task_authorities, _final_plan, _manifest
from tests.unit.test_controlled_study import implementation_payload
from tests.unit.test_study_execution import (
    FakeEvaluatedMatrixAdapter,
    FakeStudyAttemptAdapter,
)
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


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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


def _evaluated_study() -> tuple[
    dict[str, object],
    ControlledRunPlanV2,
    tuple[ControlledTaskAuthority, ...],
]:
    manifest = _manifest()
    tasks = tuple(sorted(_all_task_authorities(manifest), key=lambda item: item.case.case_id))
    run_plan = _final_plan(manifest, task_authorities=tasks)
    cases = [
        {
            "case_id": item.case.case_id,
            "split": item.split_id,
            "authority_sha256": case_authority_sha256(run_plan, item.case.case_id),
        }
        for item in tasks
    ]
    development_case = next(item for item in cases if item["split"] == "development")
    regression_case = next(item for item in cases if item["split"] == "regression")
    development = freeze_candidate_development(
        {
            "schema_version": "cernora.reference.candidate-development/v1",
            "baseline": {
                "configuration_id": "baseline",
                "authority_sha256": configuration_authority_sha256(run_plan, "baseline"),
            },
            "candidate": {
                "configuration_id": "candidate",
                "baseline_authority_sha256": configuration_authority_sha256(run_plan, "baseline"),
                "authority_sha256": configuration_authority_sha256(run_plan, "candidate"),
                "treatment_axis": "prompt-instruction",
                "treatment_sha256": "c" * 64,
            },
            "hypothesis": {
                "observed_failure_code": "interval-boundary-v1",
                "mechanism": "The agent misses inclusive endpoint overlap.",
                "intervention_scope": "Prompt guidance for interval repair reasoning.",
                "expected_observation": "Fewer held-out boundary failures.",
                "falsifier": "No held-out improvement or a regression.",
            },
            "observations": [
                {
                    "observation_id": "agent-failure-001",
                    "case_id": development_case["case_id"],
                    "split": "development",
                    "source": "agent-pilot",
                    "agent_outcome": "behavioral-failure",
                    "failure_code": "interval-boundary-v1",
                    "evidence_sha256": "d" * 64,
                },
                {
                    "observation_id": "regression-calibration-001",
                    "case_id": regression_case["case_id"],
                    "split": "regression",
                    "source": "verifier-calibration",
                    "agent_outcome": "not-observed",
                    "failure_code": None,
                    "evidence_sha256": "e" * 64,
                },
            ],
        }
    )
    policy = materialize_study_analysis_policy(
        {
            "schema_version": "cernora.reference.study-analysis-policy/v1",
            "primary_outcome": "paired-reliable-success-rate-delta",
            "bootstrap_resamples": 10000,
            "confidence_level": "0.95",
            "guardrail_rule": "no-protected-regression",
            "missing_evidence": "inconclusive",
            "claim_source": "held-out-only",
        }
    )
    heldout_cases = [item for item in cases if item["split"] == "held-out"]
    intent_payload: dict[str, object] = {
        "schema_version": "cernora.reference.study-intent/v1",
        "study_kind": "confirmatory-effect",
        "candidate_development": development.model_dump(mode="json"),
        "cases": cases,
        "repetitions": run_plan.repetitions,
        "max_attempt_count": run_plan.execution.max_attempt_count,
        "max_wall_seconds": run_plan.execution.max_total_wall_time_seconds,
        "heldout_commitment": materialize_heldout_commitment(
            {
                "schema_version": "cernora.reference.heldout-commitment/v1",
                "manifest_sha256": "7" * 64,
                "case_count": len(heldout_cases),
                "case_commitment_root_sha256": sha256_bytes(canonical_json_bytes(heldout_cases)),
                "reveal_policy_sha256": "9" * 64,
            }
        ).model_dump(mode="json"),
        "analysis_policy": policy.model_dump(mode="json"),
        "implementation_lock": materialize_implementation_lock(
            implementation_payload(
                analysis_sha256=sha256_bytes(canonical_json_bytes(policy.model_dump(mode="json")))
            )
        ).model_dump(mode="json"),
    }
    return intent_payload, run_plan, tasks


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


def test_operator_pause_publishes_diagnostic_only_pack_and_resumes(
    tmp_path: Path,
) -> None:
    destination = _custody_destination(tmp_path, "pause")

    try:
        _, running = _start_execution(destination)
        pause_directive = {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "step-execution",
            "expected_state_id": running.state_id,
        }
        adapter = FakeStudyAttemptAdapter()

        paused = advance(
            destination,
            pause_directive,
            executor=adapter,
            should_stop=lambda: True,
        )
        repeated = advance(destination, pause_directive, executor=adapter)

        assert isinstance(paused, PausedOutcome)
        assert repeated == paused
        assert paused.reason == "operator-request"
        assert not adapter.requests
        artifact_root = destination / "artifacts" / paused.artifact.artifact_id
        manifest = StudyArtifactManifest.model_validate_json(
            (artifact_root / "manifest.json").read_bytes()
        )
        assert manifest.kind == "diagnostic-pack"
        assert manifest.claim_authority == "diagnostic-only"
        assert manifest.batch_package_sha256 is None
        assert manifest.comparison_package_sha256 is None
        assert all(
            "batch" not in item.path and "comparison" not in item.path for item in manifest.files
        )
        rebuilt = tmp_path / "rebuilt-diagnostic"
        assert rebuild(artifact_root, rebuilt) == manifest
        assert _tree_bytes(rebuilt) == _tree_bytes(artifact_root)

        resumed = advance(
            destination,
            {
                "schema_version": "cernora.reference.advance-directive/v1",
                "action": "step-execution",
                "expected_state_id": paused.state_id,
            },
            executor=adapter,
        )

        assert isinstance(resumed, RunningOutcome)
        assert len(adapter.requests) == 1
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_ambiguous_runtime_attempt_pauses_without_retry(tmp_path: Path) -> None:
    destination = _custody_destination(tmp_path, "ambiguous")

    class ActiveAdapter:
        def __init__(self) -> None:
            self.execute_count = 0
            self.reconcile_count = 0

        def execute(self, request: _AttemptRequest) -> None:
            self.execute_count += 1
            raise RuntimeError("simulated interrupted Runtime")

        def reconcile(self, request: _AttemptRequest) -> Literal["active"]:
            self.reconcile_count += 1
            return "active"

    try:
        _, running = _start_execution(destination)
        directive = {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "step-execution",
            "expected_state_id": running.state_id,
        }
        adapter = ActiveAdapter()

        with pytest.raises(RuntimeError, match="interrupted Runtime"):
            advance(destination, directive, executor=adapter)

        paused = advance(destination, directive, executor=adapter)

        assert isinstance(paused, PausedOutcome)
        assert paused.reason == "ambiguous-active-attempt"
        assert paused.artifact.kind == "diagnostic-pack"
        assert adapter.execute_count == 1
        assert adapter.reconcile_count == 1
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_complete_study_publishes_authoritative_core_evidence_pack(tmp_path: Path) -> None:
    destination = _custody_destination(tmp_path, "complete")

    try:
        payload, run_plan, tasks = _evaluated_study()
        intent = materialize_study_intent(payload)
        comparison_plan = _comparison_plan(
            run_plan,
            {item.case_id: item.split for item in intent.cases},
        )
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
        adapter = FakeEvaluatedMatrixAdapter(tasks, tmp_path / "evaluations")
        last_directive: dict[str, object] | None = None

        for _ in range(55):
            last_directive = {
                "schema_version": "cernora.reference.advance-directive/v1",
                "action": "step-execution",
                "expected_state_id": outcome.state_id,
            }
            outcome = advance(destination, last_directive, executor=adapter)
            if isinstance(outcome, CompletedOutcome):
                break

        assert isinstance(outcome, CompletedOutcome)
        assert len(adapter.requests) == 54
        assert outcome.artifact.kind == "evidence-pack"
        artifact = destination / "artifacts" / outcome.artifact.artifact_id
        manifest = StudyArtifactManifest.model_validate_json(
            (artifact / "manifest.json").read_bytes()
        )
        batch = reload_batch_summary_package(artifact / "batch-summary")
        comparison = reload_comparison_package(artifact / "comparison")

        assert manifest.kind == "evidence-pack"
        assert manifest.claim_authority == "confirmatory"
        assert manifest.batch_package_sha256 is not None
        assert manifest.comparison_package_sha256 is not None
        assert batch.batch_input.planned_trial_count == 54
        assert batch.batch_input.attempt_count == 54
        assert comparison.comparison_input.primary_outcome.scope == "split"
        assert comparison.comparison_input.primary_outcome.split_id == "held-out"
        rebuilt_a = tmp_path / "rebuilt-evidence-a"
        rebuilt_b = tmp_path / "rebuilt-evidence-b"
        assert rebuild(artifact, rebuilt_a) == manifest
        assert rebuild(artifact, rebuilt_b) == manifest
        assert _tree_bytes(rebuilt_a) == _tree_bytes(rebuilt_b) == _tree_bytes(artifact)
        assert last_directive is not None
        assert advance(destination, last_directive, executor=adapter) == outcome
        assert len(adapter.requests) == 54
    finally:
        shutil.rmtree(destination, ignore_errors=True)
