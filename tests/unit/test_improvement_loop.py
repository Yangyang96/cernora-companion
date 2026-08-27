from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from cernora import (
    BatchAttempt,
    BatchAttemptResources,
    BatchPlannedTrial,
    BatchSummaryPackage,
    BatchTrial,
    build_batch_summary,
    materialize_batch_input,
)

from cernora_reference_workflow.common import ContractError, canonical_content_id
from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    materialize_comparison_plan,
    materialize_treatment_declaration,
)
from cernora_reference_workflow.controlled_evaluation import materialize_repair_result
from cernora_reference_workflow.controlled_experiment_spec import (
    materialize_authority_source,
)
from cernora_reference_workflow.controlled_profile import (
    PROFILE_ID,
    PROFILE_VERSION,
    evaluate_repair_result_package,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
)
from cernora_reference_workflow.heldout_seal import (
    HeldoutManifest,
    HeldoutRevealReceipt,
)
from cernora_reference_workflow.improvement_loop import (
    CandidateFreeze,
    derive_candidate_freeze,
    verify_candidate_freeze,
    visible_corpus_digest,
)
from tests.unit.test_controlled_run_plan import valid_m4_payload

VISIBLE_ROOT = Path("examples/m4-visible")
DEV_NAMES = ("dev-integer-ledger", "dev-interval-merge", "dev-query-codec")
REG_NAMES = ("reg-csv-rollup", "reg-graph-topology", "reg-posix-normalization")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _tasks(names: tuple[str, ...]) -> tuple[ControlledTaskAuthority, ...]:
    return tuple(load_visible_task(VISIBLE_ROOT / item) for item in names)


def _pilot(tmp_path: Path, *, pass_all: bool = False) -> BatchSummaryPackage:
    tasks = _tasks(DEV_NAMES)
    run_plan_id = digest("offline-development-pilot-run-plan")
    execution_id = digest("offline-development-pilot-execution")
    planned: list[BatchPlannedTrial] = []
    trials: list[BatchTrial] = []
    for index, task in enumerate(tasks, start=1):
        source_attempt_id = digest(f"pilot-source-attempt:{task.case.case_id}:{pass_all}")
        result = materialize_repair_result(
            {
                "schema_version": "cernora.reference.repair-result/v1",
                "case_id": task.case.case_id,
                "result_record_version": "agent.evaluator.result-record/v1",
                "test_authority_sha256": digest("pilot-test-authority"),
                "test_plan_sha256": digest("pilot-test-plan"),
                "test_source_sha256": task.test_source_sha256,
                "termination": "exited",
                "exit_code": 0 if pass_all else 1,
                "checks": [
                    {
                        "check_id": "frozen-verifier",
                        "failure_code": task.failure_code,
                        "passed": pass_all,
                    }
                ],
                "allowed_paths": list(task.allowed_paths),
                "changed_paths": [],
                "protected_paths": list(task.protected_paths),
                "protected_path_receipt": {
                    "before_sha256": digest(f"protected:{task.case.case_id}"),
                    "after_sha256": digest(f"protected:{task.case.case_id}"),
                    "unchanged": True,
                },
            }
        )
        package = evaluate_repair_result_package(
            task=task,
            tasks=tasks,
            result=result,
            source_attempt_id=source_attempt_id,
            output=tmp_path / f"pilot-evaluation-{index}",
        )
        trial_slot_id = digest(f"pilot-slot:{task.case.case_id}")
        trial_id = digest(f"pilot-trial:{task.case.case_id}")
        experiment_id = digest(f"pilot-experiment:{task.case.case_id}")
        attempt_id = digest(f"pilot-attempt:{task.case.case_id}:{pass_all}")
        planned.append(
            BatchPlannedTrial(
                slot_index=index,
                trial_slot_id=trial_slot_id,
                case_id=task.case.case_id,
                configuration_id="baseline",
                experiment_id=experiment_id,
                repetition=1,
            )
        )
        attempt = BatchAttempt(
            schema_version="agent.evaluator.batch-attempt/v1",
            attempt_id=attempt_id,
            source_attempt_id=source_attempt_id,
            trial_id=trial_id,
            ordinal=1,
            predecessor_attempt_id=None,
            source_manifest_sha256=digest(f"pilot-manifest:{task.case.case_id}"),
            retry_eligible=False,
            resources=BatchAttemptResources(),
            evaluation=package,
            lifecycle=None,
        )
        trials.append(
            BatchTrial(
                schema_version="agent.evaluator.batch-trial/v1",
                run_plan_id=run_plan_id,
                execution_id=execution_id,
                trial_id=trial_id,
                slot_index=index,
                trial_slot_id=trial_slot_id,
                case_id=task.case.case_id,
                configuration_id="baseline",
                experiment_id=experiment_id,
                repetition=1,
                selected_attempt_id=attempt_id,
                attempts=(attempt,),
            )
        )
    batch = materialize_batch_input(
        {
            "schema_version": "agent.evaluator.batch-input/v1",
            "run_plan_id": run_plan_id,
            "execution_id": execution_id,
            "execution_status": "completed",
            "budget_status": "within_budget",
            "companion_version": "0.4.0-offline-pilot",
            "planned_trial_count": 3,
            "attempt_count": 3,
            "planned_trials": [item.model_dump(mode="json") for item in planned],
            "trials": [item.model_dump(mode="json") for item in trials],
        }
    )
    return BatchSummaryPackage(batch_input=batch, summary=build_batch_summary(batch))


def _manifest() -> HeldoutManifest:
    return HeldoutManifest.from_file(Path("examples/m4-heldout-sealed/manifest.json"))


def _final_plan(
    manifest: HeldoutManifest,
    *,
    candidate_prompt: str = "Inspect the leading failure, then repair the project.",
) -> ControlledRunPlanV2:
    case_ids = tuple(
        sorted((*DEV_NAMES, *REG_NAMES, *(item.case_id for item in manifest.case_commitments)))
    )
    return materialize_controlled_run_plan(
        valid_m4_payload(
            case_ids=case_ids,
            candidate_prompt=candidate_prompt,
            candidate_failure_binding={
                "code": "interval_boundary_v1",
                "profile_id": PROFILE_ID,
                "profile_version": PROFILE_VERSION,
            },
        )
    )


def _comparison(plan: ControlledRunPlanV2, manifest: HeldoutManifest) -> ComparisonPlanV1:
    split_by_case = {
        **{item: "development" for item in DEV_NAMES},
        **{item: "regression" for item in REG_NAMES},
        **{item.case_id: "held-out" for item in manifest.case_commitments},
    }
    statistics = plan.experiment_specs[0].statistical_policy
    return materialize_comparison_plan(
        {
            "schema_version": "cernora.reference.comparison-plan/v1",
            "source_run_plan_id": plan.run_plan_id,
            "baseline_configuration_id": "baseline",
            "candidate_configuration_id": "candidate",
            "case_splits": [
                {"case_id": item.case_id, "split_id": split_by_case[item.case_id]}
                for item in plan.cases
            ],
            "treatment": materialize_treatment_declaration(("prompt_instruction",)).model_dump(
                mode="json"
            ),
            "primary_outcome": {
                "metric": "reliable_success_rate",
                "scope": "all",
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
                },
                {
                    "guardrail_id": "protected-paths",
                    "hard": True,
                    "metric": "profile_failure_code_rate",
                    "scope": "all",
                    "split_id": None,
                    "direction": "lower_is_better",
                    "max_adverse_basis_points": 0,
                    "profile_id": PROFILE_ID,
                    "profile_version": PROFILE_VERSION,
                    "failure_code": "protected_paths_unchanged_v1",
                },
                {
                    "guardrail_id": "regression-rsr",
                    "hard": True,
                    "metric": "reliable_success_rate",
                    "scope": "split",
                    "split_id": "regression",
                    "direction": "higher_is_better",
                    "max_adverse_basis_points": 1000,
                    "profile_id": None,
                    "profile_version": None,
                    "failure_code": None,
                },
            ],
            "bootstrap": statistics.bootstrap.model_dump(mode="json"),
            "pass_k": (
                None if statistics.pass_k is None else statistics.pass_k.model_dump(mode="json")
            ),
            "statistical_policy": statistics.model_dump(mode="json"),
        }
    )


def _reveal(manifest: HeldoutManifest, freeze: CandidateFreeze) -> HeldoutRevealReceipt:
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.heldout-reveal-receipt/v1",
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        "candidate_freeze_id": freeze.candidate_freeze_id,
        "candidate_freeze_sha256": freeze.candidate_freeze_sha256,
        "revealed_archive_sha256": manifest.archive_sha256,
        "case_records": [
            {
                "case_id": item.case_id,
                "sealed_plaintext_sha256": item.plaintext_sha256,
                "revealed_authority_sha256": item.plaintext_sha256,
            }
            for item in manifest.case_commitments
        ],
    }
    payload["receipt_id"] = canonical_content_id(payload, excluded=frozenset())
    return HeldoutRevealReceipt.model_validate(payload)


def _freeze_and_final(
    tmp_path: Path,
) -> tuple[
    CandidateFreeze,
    BatchSummaryPackage,
    ControlledRunPlanV2,
    ComparisonPlanV1,
    HeldoutManifest,
]:
    manifest = _manifest()
    plan = _final_plan(manifest)
    pilot = _pilot(tmp_path)
    baseline = next(
        item.prompt_source for item in plan.experiment_specs if item.configuration_id == "baseline"
    )
    candidate = next(
        item.prompt_source for item in plan.experiment_specs if item.configuration_id == "candidate"
    )
    freeze = derive_candidate_freeze(
        pilot_package=pilot,
        baseline_prompt_authority=baseline,
        candidate_prompt_authority=candidate,
        visible_corpus_root=VISIBLE_ROOT,
        heldout_manifest=manifest,
    )
    return freeze, pilot, plan, _comparison(plan, manifest), manifest


def test_candidate_freeze_is_derived_from_real_strict_three_case_pilot(
    tmp_path: Path,
) -> None:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    reveal = _reveal(manifest, freeze)

    verify_candidate_freeze(
        freeze,
        pilot_package=pilot,
        run_plan=plan,
        comparison_plan=comparison,
        visible_corpus_root=VISIBLE_ROOT,
        heldout_manifest=manifest,
        reveal_receipt=reveal,
    )

    assert freeze.pilot.prohibited_from_final_live_batch is True
    assert freeze.pilot.development_case_ids == tuple(sorted(DEV_NAMES))
    assert freeze.leading_failure.code == "interval_boundary_v1"
    assert freeze.leading_failure.count == 1
    assert len(plan.expand_trial_slots()) == 54


@pytest.mark.parametrize("drift", ("freeze", "run-plan", "policy", "seal", "reveal"))
def test_final_verifier_rejects_freeze_plan_policy_seal_and_reveal_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    reveal = _reveal(manifest, freeze)
    if drift == "freeze":
        freeze = freeze.model_copy(update={"visible_corpus_sha256": "0" * 64})
    elif drift == "run-plan":
        plan = _final_plan(manifest, candidate_prompt="Apply an unrelated candidate prompt.")
        comparison = _comparison(plan, manifest)
    elif drift == "policy":
        payload = comparison.model_dump(
            mode="json", exclude={"comparison_plan_id", "comparison_plan_sha256"}
        )
        primary = payload["primary_outcome"]
        assert isinstance(primary, dict)
        primary["practical_threshold_basis_points"] = 999
        comparison = materialize_comparison_plan(payload)
    elif drift == "seal":
        manifest = manifest.model_copy(update={"ciphertext_sha256": "0" * 64})
    else:
        reveal = reveal.model_copy(update={"candidate_freeze_sha256": "0" * 64})

    with pytest.raises(ContractError):
        verify_candidate_freeze(
            freeze,
            pilot_package=pilot,
            run_plan=plan,
            comparison_plan=comparison,
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
            reveal_receipt=reveal,
        )


def _canonically_reidentify_freeze(
    freeze: CandidateFreeze,
    *,
    mutation: str,
) -> CandidateFreeze:
    payload = freeze.model_dump(
        mode="json", exclude={"candidate_freeze_id", "candidate_freeze_sha256"}
    )
    pilot = payload["pilot"]
    leading = payload["leading_failure"]
    assert isinstance(pilot, dict)
    assert isinstance(leading, dict)
    if mutation == "leading":
        leading["code"] = "ledger_cents_sign_v1"
        candidate = materialize_authority_source(
            "treatment-prompt",
            {
                "selected_failure": {
                    "code": leading["code"],
                    "profile_id": leading["profile_id"],
                    "profile_version": leading["profile_version"],
                },
                "text": "Inspect the leading failure, then repair the project.",
            },
        )
        payload["candidate_prompt_authority"] = candidate.model_dump(mode="json")
    elif mutation == "development-cases":
        pilot["development_case_ids"] = [
            "dev-integer-ledger",
            "dev-interval-merge",
            "reg-csv-rollup",
        ]
    else:
        pilot["summary_id"] = "batch-summary-canonically-reidentified-tamper"
        pilot["summary_sha256"] = "0" * 64
    pilot["pilot_id"] = canonical_content_id(pilot, excluded=frozenset({"pilot_id"}))
    identity = canonical_content_id(payload, excluded=frozenset())
    payload["candidate_freeze_id"] = f"candidate-freeze-{identity}"
    payload["candidate_freeze_sha256"] = identity
    return CandidateFreeze.model_validate(payload)


@pytest.mark.parametrize("mutation", ("leading", "development-cases", "pilot"))
def test_final_verifier_rejects_canonically_reidentified_pilot_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    forged = _canonically_reidentify_freeze(freeze, mutation=mutation)
    reveal = _reveal(manifest, forged)

    with pytest.raises(ContractError, match="exact derivation|development split"):
        verify_candidate_freeze(
            forged,
            pilot_package=pilot,
            run_plan=plan,
            comparison_plan=comparison,
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
            reveal_receipt=reveal,
        )


def test_no_development_failure_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest()
    plan = _final_plan(manifest)
    with pytest.raises(ContractError, match="no usable"):
        derive_candidate_freeze(
            pilot_package=_pilot(tmp_path, pass_all=True),
            baseline_prompt_authority=next(
                item.prompt_source
                for item in plan.experiment_specs
                if item.configuration_id == "baseline"
            ),
            candidate_prompt_authority=next(
                item.prompt_source
                for item in plan.experiment_specs
                if item.configuration_id == "candidate"
            ),
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
        )


def test_visible_corpus_digest_is_content_identified(tmp_path: Path) -> None:
    source = tmp_path / "visible"
    shutil.copytree(VISIBLE_ROOT, source)
    first = visible_corpus_digest(source)
    (source / "dev-interval-merge" / "solution.py").write_text(
        "def merge(value): return value\n", encoding="utf-8"
    )

    assert visible_corpus_digest(source) != first
