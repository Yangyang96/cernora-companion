from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from cernora import (
    BatchAttempt,
    BatchAttemptResources,
    BatchEvaluationPackage,
    BatchInput,
    BatchLifecycleRecord,
    BatchPlannedTrial,
    BatchTrial,
    CompletedExport,
    check_adapter_conformance,
    embed_evaluation_package,
    evaluate_imported_case,
    import_evidence_bundle_v2,
    materialize_batch_input,
    reload_batch_summary_package,
    reload_comparison_package,
    summarize_batch,
)

from cernora_reference_workflow.adapter import ReferenceCodingAdapter
from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.comparison_input import (
    ComparisonConfigurationError,
    _validate_receipt_bindings,
    assemble_comparison_input,
    assemble_comparison_input_from_paths,
    compare_batch_summary,
)
from cernora_reference_workflow.comparison_plan import ComparisonPlanV1, materialize_comparison_plan
from cernora_reference_workflow.controlled_experiment_spec import (
    ExpectedEvaluationAuthoritySource,
    ExpectedEvaluationPolicySource,
    materialize_controlled_experiment_spec,
    materialize_expected_evaluation_authority,
    materialize_expected_evaluation_policy,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.export import publish_completed_export
from cernora_reference_workflow.profile import create_profile
from cernora_reference_workflow.run_plan import RunPlan
from tests.unit.test_comparison_plan import valid_payload as valid_comparison_payload
from tests.unit.test_controlled_experiment_spec import valid_payload as valid_spec_payload
from tests.unit.test_controlled_run_plan import valid_payload as valid_run_plan_payload
from tests.unit.test_execution import small_plan
from tests.unit.test_export import materialize_staging


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload[key]
    assert isinstance(value, dict)
    return value


def _lifecycle_batch(run_plan: ControlledRunPlanV2) -> BatchInput:
    execution_id = _digest("controlled-execution")
    planned: list[BatchPlannedTrial] = []
    trials: list[BatchTrial] = []
    for slot in run_plan.expand_trial_slots():
        trial_id = _digest(f"trial:{slot.trial_slot_id}")
        attempt_id = _digest(f"attempt:{slot.trial_slot_id}")
        planned.append(
            BatchPlannedTrial(
                slot_index=slot.slot_index,
                trial_slot_id=slot.trial_slot_id,
                case_id=slot.case_id,
                configuration_id=slot.configuration_id,
                experiment_id=slot.experiment_id,
                repetition=slot.repetition,
            )
        )
        attempt = BatchAttempt(
            schema_version="agent.evaluator.batch-attempt/v1",
            attempt_id=attempt_id,
            source_attempt_id=f"source-{slot.slot_index}",
            trial_id=trial_id,
            ordinal=1,
            predecessor_attempt_id=None,
            source_manifest_sha256=_digest(f"manifest:{slot.trial_slot_id}"),
            retry_eligible=False,
            resources=BatchAttemptResources(),
            lifecycle=BatchLifecycleRecord(
                schema_version="agent.evaluator.batch-lifecycle/v1",
                category="other_verified_infrastructure_failure",
                retry_eligible=False,
                source_state="infrastructure-unavailable",
                receipt_sha256=_digest(f"receipt:{slot.trial_slot_id}"),
            ),
        )
        trials.append(
            BatchTrial(
                schema_version="agent.evaluator.batch-trial/v1",
                run_plan_id=run_plan.run_plan_id,
                execution_id=execution_id,
                trial_id=trial_id,
                slot_index=slot.slot_index,
                trial_slot_id=slot.trial_slot_id,
                case_id=slot.case_id,
                configuration_id=slot.configuration_id,
                experiment_id=slot.experiment_id,
                repetition=slot.repetition,
                selected_attempt_id=attempt_id,
                attempts=(attempt,),
            )
        )
    return materialize_batch_input(
        {
            "schema_version": "agent.evaluator.batch-input/v1",
            "run_plan_id": run_plan.run_plan_id,
            "execution_id": execution_id,
            "execution_status": "completed",
            "budget_status": "within_budget",
            "companion_version": run_plan.companion_version,
            "planned_trial_count": len(planned),
            "attempt_count": len(trials),
            "planned_trials": [item.model_dump(mode="json") for item in planned],
            "trials": [item.model_dump(mode="json") for item in trials],
        }
    )


def _inputs(
    tmp_path: Path,
) -> tuple[Path, Path, Path, ControlledRunPlanV2, ComparisonPlanV1]:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    comparison_plan = materialize_comparison_plan(valid_comparison_payload(run_plan))
    batch_input = _lifecycle_batch(run_plan)
    batch_root = tmp_path / "batch-summary"
    summarize_batch(batch_input, batch_root)
    run_plan_path = tmp_path / "controlled-run-plan.json"
    comparison_plan_path = tmp_path / "comparison-plan.json"
    run_plan_path.write_bytes(run_plan.canonical_bytes())
    comparison_plan_path.write_bytes(comparison_plan.canonical_bytes())
    return batch_root, run_plan_path, comparison_plan_path, run_plan, comparison_plan


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_comparison_assembly_is_authority_bound_reloadable_and_byte_identical(
    tmp_path: Path,
) -> None:
    batch, run_plan_path, plan_path, _, _ = _inputs(tmp_path)
    trees: list[dict[str, bytes]] = []
    identities: list[tuple[str, str]] = []

    for index in range(3):
        output = tmp_path / f"comparison-{index}"
        summary = compare_batch_summary(batch, run_plan_path, plan_path, output)
        package = reload_comparison_package(output)
        assert package.summary == summary
        assert package.comparison_input.treatment.changes[0].kind == "prompt_instruction"
        assert package.summary.conclusion == "uncertain"
        authoritative = canonical_json_bytes(package.model_dump(mode="json")).lower()
        assert all(word not in authoritative for word in (b"winner", b"ranking", b"promotion"))
        trees.append(_tree_bytes(output))
        identities.append((summary.comparison_id, summary.summary_id))

    assert trees[0] == trees[1] == trees[2]
    assert identities[0] == identities[1] == identities[2]


def test_assembly_rejects_batch_slot_authority_swap(tmp_path: Path) -> None:
    batch_root, _, _, run_plan, comparison_plan = _inputs(tmp_path)
    second_batch = tmp_path / "second-batch"
    summarize_batch(_lifecycle_batch(run_plan), second_batch)
    batch_package = reload_batch_summary_package(second_batch)
    payload = batch_package.batch_input.model_dump(
        mode="json", exclude={"batch_input_id", "batch_input_sha256"}
    )
    replacement = payload["planned_trials"][3]["experiment_id"]
    for index in range(3):
        payload["planned_trials"][index]["experiment_id"] = replacement
        payload["trials"][index]["experiment_id"] = replacement
    swapped = materialize_batch_input(payload)
    swapped_root = tmp_path / "swapped-batch"
    summarize_batch(swapped, swapped_root)
    swapped_package = reload_batch_summary_package(swapped_root)

    with pytest.raises(ContractError, match="Trial slots do not equal"):
        assemble_comparison_input(swapped_package, run_plan, comparison_plan)
    assert batch_root.is_dir()


@pytest.mark.parametrize(
    "field",
    (
        "timeout_seconds",
        "memory_megabytes",
        "retry_max_attempts",
        "dataset",
        "profile",
        "report",
        "statistics",
    ),
)
def test_assembly_rejects_hidden_invariant_differences_before_execution(
    tmp_path: Path, field: str
) -> None:
    run_payload = valid_run_plan_payload()
    specifications = run_payload["experiment_specs"]
    cells = run_payload["cells"]
    assert isinstance(specifications, list)
    assert isinstance(cells, list)
    dataset = specifications[0]["dataset_authority"]
    with pytest.raises((ContractError, ValueError)):
        for index, case_id in ((1, "repair-case-1"), (3, "repair-case-2")):
            source = valid_spec_payload(
                case_id=case_id,
                configuration_id="candidate",
                prompt="Inspect the failure first, then repair the project.",
            )
            source["dataset_authority"] = deepcopy(dataset)
            if field == "timeout_seconds":
                _mapping(source, "limits")["timeout_seconds"] = 301
            elif field == "memory_megabytes":
                _mapping(source, "limits")["memory_mebibytes"] = 4097
            elif field == "retry_max_attempts":
                _mapping(source, "retry")["max_retries"] = 2
            elif field == "dataset":
                source["dataset_authority"] = valid_spec_payload(
                    case_id=case_id,
                    configuration_id="candidate",
                    prompt="Inspect the failure first, then repair the project.",
                )["dataset_authority"]
            elif field == "profile":
                _mapping(source, "profile")["profile_version"] = "1.0.1"
            elif field == "report":
                _mapping(source, "workflow")["report"] = "cernora-reference-run-report/v2"
            else:
                policy = _mapping(source, "statistical_policy")
                pass_k = policy["pass_k"]
                assert isinstance(pass_k, dict)
                pass_k["k"] = 1
            spec = materialize_controlled_experiment_spec(source)
            specifications[index] = spec.model_dump(mode="json")
            cells[index]["experiment_id"] = spec.experiment_id
        drifted = materialize_controlled_run_plan(run_payload)
        comparison = materialize_comparison_plan(valid_comparison_payload(drifted))
        comparison.validate_run_plan(drifted)


def test_assembly_rejects_comparison_plan_bound_to_another_run_plan(tmp_path: Path) -> None:
    _, _, _, run_plan, comparison_plan = _inputs(tmp_path)
    changed = valid_run_plan_payload()
    _mapping(changed, "execution")["max_total_wall_time_seconds"] = 43199
    other = materialize_controlled_run_plan(changed)
    other_root = tmp_path / "other-batch"
    summarize_batch(_lifecycle_batch(other), other_root)
    batch_package = reload_batch_summary_package(other_root)

    with pytest.raises(ComparisonConfigurationError, match="does not bind"):
        assemble_comparison_input(batch_package, other, comparison_plan)


def test_m3_path_rejects_real_legacy_v1_plan_without_rewriting_it(tmp_path: Path) -> None:
    batch, _, comparison_path, _, _ = _inputs(tmp_path)
    legacy = small_plan()
    legacy_path = tmp_path / "legacy-run-plan.json"
    legacy_path.write_bytes(legacy.canonical_bytes())

    assert RunPlan.from_file(legacy_path) == legacy
    with pytest.raises(ComparisonConfigurationError, match="controlled RunPlan v2"):
        assemble_comparison_input_from_paths(batch, legacy_path, comparison_path)
    assert RunPlan.from_file(legacy_path) == legacy


def _strict_evaluated_package(tmp_path: Path) -> BatchEvaluationPackage:
    staging = tmp_path / "staging"
    completed_export = tmp_path / "completed-export"
    fields = materialize_staging(staging)
    publish_completed_export(staging, completed_export, manifest_fields=fields)
    profile = create_profile()
    adapter = ReferenceCodingAdapter(profile)
    adapted = check_adapter_conformance(
        adapter,
        CompletedExport(root=completed_export),
        tmp_path / "adapted",
    )
    imported = tmp_path / "imported"
    import_evidence_bundle_v2(
        profile=profile,
        bundle_path=adapted.bundle_path,
        output=imported,
    )
    evaluated = tmp_path / "evaluated"
    evaluate_imported_case(profile, imported, evaluated)
    return embed_evaluation_package(evaluated)


def _strict_evaluated_binding(
    tmp_path: Path,
) -> tuple[
    BatchInput,
    ExpectedEvaluationAuthoritySource,
    ExpectedEvaluationPolicySource,
    str,
    str,
]:
    package = _strict_evaluated_package(tmp_path)
    receipt = cast(dict[str, Any], json.loads(package.file_payloads()["evaluation-receipt.json"]))
    authority = ExpectedEvaluationAuthoritySource.model_validate(receipt["authority"])
    policy = materialize_expected_evaluation_policy(
        {
            "schema_version": "agent.evaluator.comparison-evaluation-policy/v1",
            "profile": receipt["profile"],
            "projection": receipt["authority"]["projection"],
            "scorer": receipt["scorer"],
            "case_gate": receipt["case_gate"],
        }
    )
    case_id = cast(str, receipt["case"]["case_id"])
    configuration_id = "baseline"
    trial_id = _digest("strict-evaluated-trial")
    attempt_id = _digest("strict-evaluated-attempt")
    experiment_id = _digest("strict-evaluated-experiment")
    run_plan_id = _digest("strict-evaluated-run-plan")
    execution_id = _digest("strict-evaluated-execution")
    slot = BatchPlannedTrial(
        slot_index=1,
        trial_slot_id=_digest("strict-evaluated-slot"),
        case_id=case_id,
        configuration_id=configuration_id,
        experiment_id=experiment_id,
        repetition=1,
    )
    attempt = BatchAttempt(
        schema_version="agent.evaluator.batch-attempt/v1",
        attempt_id=attempt_id,
        source_attempt_id=cast(str, receipt["run"]["attempt_id"]),
        trial_id=trial_id,
        ordinal=1,
        predecessor_attempt_id=None,
        source_manifest_sha256=_digest("strict-evaluated-manifest"),
        retry_eligible=False,
        resources=BatchAttemptResources(),
        evaluation=package,
    )
    trial = BatchTrial(
        schema_version="agent.evaluator.batch-trial/v1",
        run_plan_id=run_plan_id,
        execution_id=execution_id,
        trial_id=trial_id,
        slot_index=1,
        trial_slot_id=slot.trial_slot_id,
        case_id=case_id,
        configuration_id=configuration_id,
        experiment_id=experiment_id,
        repetition=1,
        selected_attempt_id=attempt_id,
        attempts=(attempt,),
    )
    batch = materialize_batch_input(
        {
            "schema_version": "agent.evaluator.batch-input/v1",
            "run_plan_id": run_plan_id,
            "execution_id": execution_id,
            "execution_status": "completed",
            "budget_status": "within_budget",
            "companion_version": "0.3.0",
            "planned_trial_count": 1,
            "attempt_count": 1,
            "planned_trials": [slot.model_dump(mode="json")],
            "trials": [trial.model_dump(mode="json")],
        }
    )
    return batch, authority, policy, case_id, configuration_id


def _receipt_binding_plan(
    *,
    authority: ExpectedEvaluationAuthoritySource,
    policy: ExpectedEvaluationPolicySource,
    case_id: str,
    configuration_id: str,
) -> ControlledRunPlanV2:
    specification = SimpleNamespace(
        task=SimpleNamespace(task_id=case_id),
        configuration_id=configuration_id,
        expected_evaluation_authority=authority,
        expected_evaluation_policy=policy,
    )
    return cast(ControlledRunPlanV2, SimpleNamespace(experiment_specs=(specification,)))


@pytest.mark.parametrize("mutation", (None, "authority", "policy"))
def test_strict_evaluated_batch_receipt_matches_exact_authority_and_policy(
    tmp_path: Path, mutation: str | None
) -> None:
    batch, authority, policy, case_id, configuration_id = _strict_evaluated_binding(tmp_path)
    if mutation == "authority":
        payload = authority.model_dump(mode="json", exclude={"authority_id", "authority_sha256"})
        fixtures = payload["fixtures"]
        assert isinstance(fixtures, list)
        fixtures[0]["sha256"] = "0" * 64
        authority = materialize_expected_evaluation_authority(payload)
    elif mutation == "policy":
        payload = policy.model_dump(mode="json", exclude={"policy_sha256"})
        profile = payload["profile"]
        assert isinstance(profile, dict)
        profile["sha256"] = "0" * 64
        policy = materialize_expected_evaluation_policy(payload)
    run_plan = _receipt_binding_plan(
        authority=authority,
        policy=policy,
        case_id=case_id,
        configuration_id=configuration_id,
    )

    if mutation is None:
        _validate_receipt_bindings(batch, run_plan)
    else:
        with pytest.raises(ComparisonConfigurationError, match=f"Evaluation {mutation}"):
            _validate_receipt_bindings(batch, run_plan)
