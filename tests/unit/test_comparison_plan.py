from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    materialize_comparison_plan,
    materialize_treatment_declaration,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    materialize_controlled_experiment_spec,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from tests.replacement import install_file_replacement
from tests.unit.test_controlled_experiment_spec import valid_payload as valid_spec_payload
from tests.unit.test_controlled_run_plan import valid_payload as valid_run_plan_payload


def valid_payload(run_plan: ControlledRunPlanV2) -> dict[str, object]:
    statistics = run_plan.experiment_specs[0].statistical_policy
    return {
        "schema_version": "cernora.reference.comparison-plan/v1",
        "source_run_plan_id": run_plan.run_plan_id,
        "baseline_configuration_id": "baseline",
        "candidate_configuration_id": "candidate",
        "case_splits": [
            {"case_id": "repair-case-1", "split_id": "development"},
            {"case_id": "repair-case-2", "split_id": "regression"},
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
                "guardrail_id": "regression-leading-failure",
                "hard": True,
                "metric": "profile_failure_code_rate",
                "scope": "split",
                "split_id": "regression",
                "direction": "lower_is_better",
                "max_adverse_basis_points": 0,
                "profile_id": "cernora-reference-coding-v1",
                "profile_version": "1.0.0",
                "failure_code": "incorrect-repair",
            },
            {
                "guardrail_id": "reliable-success",
                "hard": True,
                "metric": "reliable_success_rate",
                "scope": "all",
                "split_id": None,
                "direction": "higher_is_better",
                "max_adverse_basis_points": 0,
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


def test_comparison_plan_derives_treatment_endpoints_from_authorities() -> None:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    comparison = materialize_comparison_plan(valid_payload(run_plan))
    treatment = comparison.materialize_core_treatment(run_plan)

    assert treatment.changes[0].kind == "prompt_instruction"
    baseline = next(
        item for item in run_plan.experiment_specs if item.configuration_id == "baseline"
    )
    candidate = next(
        item for item in run_plan.experiment_specs if item.configuration_id == "candidate"
    )
    assert treatment.changes[0].baseline_sha256 == (
        baseline.core_projection().prompt_instruction_sha256
    )
    assert treatment.changes[0].candidate_sha256 == (
        candidate.core_projection().prompt_instruction_sha256
    )


def test_comparison_plan_identity_is_deterministic_and_source_bound() -> None:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    first = materialize_comparison_plan(valid_payload(run_plan))
    second = materialize_comparison_plan(deepcopy(valid_payload(run_plan)))

    assert first == second
    first.validate_run_plan(run_plan)
    changed = valid_run_plan_payload()
    execution = changed["execution"]
    assert isinstance(execution, dict)
    execution["max_total_wall_time_seconds"] = 43199
    other = materialize_controlled_run_plan(changed)
    with pytest.raises(ContractError, match="does not bind"):
        first.validate_run_plan(other)


def test_comparison_plan_rejects_primary_scope_outside_frozen_case_splits() -> None:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    payload = valid_payload(run_plan)
    payload["primary_outcome"] = {
        "metric": "reliable_success_rate",
        "scope": "split",
        "split_id": "held-out",
        "direction": "higher_is_better",
        "practical_threshold_basis_points": 1000,
    }

    with pytest.raises(ValueError, match="Primary Outcome references an unknown split"):
        materialize_comparison_plan(payload)


def _run_plan_with_global_candidate_timeout_change() -> ControlledRunPlanV2:
    payload = valid_run_plan_payload()
    specifications = payload["experiment_specs"]
    cells = payload["cells"]
    assert isinstance(specifications, list)
    assert isinstance(cells, list)
    dataset = specifications[0]["dataset_authority"]
    for index, case_id in ((1, "repair-case-1"), (3, "repair-case-2")):
        source = valid_spec_payload(
            case_id=case_id,
            configuration_id="candidate",
            prompt="Inspect the failure first, then repair the project.",
        )
        source["dataset_authority"] = dataset
        limits = source["limits"]
        assert isinstance(limits, dict)
        limits["timeout_seconds"] = 301
        spec = materialize_controlled_experiment_spec(source)
        specifications[index] = spec.model_dump(mode="json")
        cells[index]["experiment_id"] = spec.experiment_id
    return materialize_controlled_run_plan(payload)


def test_comparison_plan_rejects_hidden_invariant_difference_before_execution() -> None:
    run_plan = _run_plan_with_global_candidate_timeout_change()
    comparison = materialize_comparison_plan(valid_payload(run_plan))

    with pytest.raises(ContractError, match="outside Treatment"):
        comparison.validate_run_plan(run_plan)


def test_comparison_plan_requires_complete_declared_treatment_difference_set() -> None:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    payload = valid_payload(run_plan)
    payload["treatment"] = materialize_treatment_declaration(
        ("model", "prompt_instruction")
    ).model_dump(mode="json")
    comparison = materialize_comparison_plan(payload)

    with pytest.raises(ContractError, match="does not equal"):
        comparison.validate_run_plan(run_plan)


@pytest.mark.parametrize("mutation", ("unknown", "case_order", "guardrail_order", "statistics"))
def test_comparison_plan_rejects_unknown_order_and_statistical_tamper(mutation: str) -> None:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    payload = valid_payload(run_plan)
    if mutation == "unknown":
        payload["winner"] = "candidate"
    elif mutation == "case_order":
        cases = payload["case_splits"]
        assert isinstance(cases, list)
        cases.reverse()
    elif mutation == "guardrail_order":
        guardrails = payload["guardrails"]
        assert isinstance(guardrails, list)
        guardrails.reverse()
    else:
        bootstrap = payload["bootstrap"]
        assert isinstance(bootstrap, dict)
        bootstrap["resamples"] = 9999
    with pytest.raises((ContractError, ValidationError, ValueError)):
        materialize_comparison_plan(payload)


def test_comparison_plan_rejects_legacy_run_plan_type() -> None:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    comparison = materialize_comparison_plan(valid_payload(run_plan))

    with pytest.raises(ContractError, match="controlled RunPlan v2"):
        comparison.validate_run_plan(cast(ControlledRunPlanV2, object()))


def test_comparison_plan_file_requires_canonical_bytes(tmp_path: Path) -> None:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    plan = materialize_comparison_plan(valid_payload(run_plan))
    canonical = tmp_path / "comparison-plan.json"
    canonical.write_bytes(plan.canonical_bytes())
    assert ComparisonPlanV1.from_file(canonical) == plan

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text("{\n" + plan.canonical_bytes().decode()[1:], encoding="utf-8")
    with pytest.raises(ContractError, match="not canonical"):
        ComparisonPlanV1.from_file(noncanonical)


@pytest.mark.parametrize("replacement", ("a-to-b", "aba"))
def test_comparison_plan_file_rejects_stable_snapshot_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    target = tmp_path / "comparison-plan.json"
    candidate = tmp_path / "other-comparison-plan.json"
    target.write_bytes(materialize_comparison_plan(valid_payload(run_plan)).canonical_bytes())
    changed = valid_payload(run_plan)
    primary = changed["primary_outcome"]
    assert isinstance(primary, dict)
    primary["practical_threshold_basis_points"] = 999
    candidate.write_bytes(materialize_comparison_plan(changed).canonical_bytes())
    install_file_replacement(
        monkeypatch, target=target, candidate=candidate, replacement=replacement
    )

    with pytest.raises(ContractError, match="changed before snapshot"):
        ComparisonPlanV1.from_file(target)


def test_comparison_plan_file_rejects_symlink(tmp_path: Path) -> None:
    run_plan = materialize_controlled_run_plan(valid_run_plan_payload())
    source = tmp_path / "source.json"
    linked = tmp_path / "linked.json"
    source.write_bytes(materialize_comparison_plan(valid_payload(run_plan)).canonical_bytes())
    linked.symlink_to(source)

    with pytest.raises(ContractError, match="ordinary file"):
        ComparisonPlanV1.from_file(linked)
