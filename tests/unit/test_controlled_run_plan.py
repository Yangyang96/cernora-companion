from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.controlled_experiment_spec import (
    ControlledExperimentSpecV2,
    DatasetCaseAuthority,
    materialize_controlled_experiment_spec,
    materialize_dataset_authority,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from tests.replacement import install_file_replacement
from tests.unit.test_controlled_experiment_spec import valid_payload as valid_spec_payload


def valid_specs() -> tuple[ControlledExperimentSpecV2, ...]:
    payloads = [
        valid_spec_payload(case_id=case_id, configuration_id=configuration_id, prompt=prompt)
        for case_id in ("repair-case-1", "repair-case-2")
        for configuration_id, prompt in (
            ("baseline", "Repair the project."),
            ("candidate", "Inspect the failure first, then repair the project."),
        )
    ]
    dataset_cases = []
    for payload in payloads[::2]:
        dataset = payload["dataset_authority"]
        assert isinstance(dataset, dict)
        cases = dataset["cases"]
        assert isinstance(cases, list)
        dataset_cases.append(DatasetCaseAuthority.model_validate(cases[0]))
    dataset = materialize_dataset_authority(tuple(dataset_cases))
    for payload in payloads:
        payload["dataset_authority"] = dataset.model_dump(mode="json")
    return tuple(materialize_controlled_experiment_spec(payload) for payload in payloads)


def valid_payload() -> dict[str, object]:
    specs = valid_specs()
    cases = [
        {
            "case_id": spec.task.task_id,
            "case_version": spec.task.task_version,
            "task_content_sha256": spec.task.content_sha256,
        }
        for spec in specs[::2]
    ]
    cells = [
        {
            "case_id": spec.task.task_id,
            "configuration_id": spec.configuration_id,
            "experiment_id": spec.experiment_id,
        }
        for spec in specs
    ]
    return {
        "schema_version": "cernora.reference.controlled-run-plan/v2",
        "companion_version": "0.3.0",
        "cernora_version": "0.1.4",
        "connector": {
            "connector_id": "cernora-reference-harbor-pi",
            "connector_version": "2",
            "platform_qualification": "macos-arm64",
        },
        "experiment_specs": [spec.model_dump(mode="json") for spec in specs],
        "cases": cases,
        "configurations": [
            {"configuration_id": "baseline"},
            {"configuration_id": "candidate"},
        ],
        "cells": cells,
        "repetitions": 3,
        "pairing_rule": "case-configuration-repetition",
        "planned_trial_count": 12,
        "worst_case_attempt_count": 24,
        "execution": {
            "concurrency": 1,
            "max_attempt_count": 24,
            "max_total_wall_time_seconds": 43200,
            "token_budget": {
                "status": "unavailable",
                "reason": "no-structured-authoritative-source",
            },
            "monetary_budget": {
                "status": "unavailable",
                "reason": "no-structured-authoritative-source",
            },
        },
        "analysis": {
            "method": "controlled-comparison",
            "method_version": "m3",
            "aggregate_quality_conclusion": False,
        },
    }


def test_v2_plan_expands_exact_authority_bound_matrix() -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    slots = plan.expand_trial_slots()

    assert len(slots) == 12
    assert tuple(item.slot_index for item in slots) == tuple(range(1, 13))
    assert tuple(item.repetition for item in slots) == (1, 2, 3) * 4
    assert len({item.trial_slot_id for item in slots}) == 12
    assert all(
        item.experiment_id in {spec.experiment_id for spec in plan.experiment_specs}
        for item in slots
    )
    assert plan == materialize_controlled_run_plan(deepcopy(valid_payload()))


def test_v2_plan_identity_changes_with_exact_attempt_budget() -> None:
    first = materialize_controlled_run_plan(valid_payload())
    changed = valid_payload()
    execution = changed["execution"]
    assert isinstance(execution, dict)
    execution["max_total_wall_time_seconds"] = 43199
    second = materialize_controlled_run_plan(changed)

    assert first.run_plan_id != second.run_plan_id
    assert (
        first.expand_trial_slots()[0].trial_slot_id != second.expand_trial_slots()[0].trial_slot_id
    )


@pytest.mark.parametrize(
    "mutation",
    ("unknown", "legacy_id", "cell_order", "missing_cell", "attempt_budget", "case_drift"),
)
def test_v2_plan_rejects_tamper_incomplete_matrix_and_cross_case_drift(mutation: str) -> None:
    payload = valid_payload()
    if mutation == "unknown":
        payload["created_at"] = "2026-08-27T00:00:00Z"
    elif mutation == "legacy_id":
        cells = payload["cells"]
        assert isinstance(cells, list)
        cells[0]["experiment_id"] = "0" * 64
    elif mutation == "cell_order":
        cells = payload["cells"]
        assert isinstance(cells, list)
        cells[0], cells[1] = cells[1], cells[0]
    elif mutation == "missing_cell":
        cells = payload["cells"]
        assert isinstance(cells, list)
        cells.pop()
        payload["planned_trial_count"] = 9
        payload["worst_case_attempt_count"] = 18
        execution = payload["execution"]
        assert isinstance(execution, dict)
        execution["max_attempt_count"] = 18
    elif mutation == "attempt_budget":
        execution = payload["execution"]
        assert isinstance(execution, dict)
        execution["max_attempt_count"] = 23
    else:
        specifications = payload["experiment_specs"]
        assert isinstance(specifications, list)
        changed = valid_spec_payload(
            case_id="repair-case-2",
            configuration_id="candidate",
            prompt="Inspect the failure first, then repair the project.",
        )
        changed["dataset_authority"] = specifications[0]["dataset_authority"]
        runtime = changed["runtime"]
        assert isinstance(runtime, dict)
        runtime["model"] = "gpt-5.6-sol"
        drifted = materialize_controlled_experiment_spec(changed)
        specifications[-1] = drifted.model_dump(mode="json")
        cells = payload["cells"]
        assert isinstance(cells, list)
        cells[-1]["experiment_id"] = drifted.experiment_id
    with pytest.raises((ContractError, ValidationError, ValueError)):
        materialize_controlled_run_plan(payload)


def test_v2_plan_file_requires_canonical_bytes(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    canonical = tmp_path / "run-plan-v2.json"
    canonical.write_bytes(plan.canonical_bytes())
    assert ControlledRunPlanV2.from_file(canonical) == plan

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text("{\n" + plan.canonical_bytes().decode()[1:], encoding="utf-8")
    with pytest.raises(ContractError, match="not canonical"):
        ControlledRunPlanV2.from_file(noncanonical)


@pytest.mark.parametrize("replacement", ("a-to-b", "aba"))
def test_v2_plan_file_rejects_stable_snapshot_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    target = tmp_path / "run-plan.json"
    candidate = tmp_path / "other-run-plan.json"
    target.write_bytes(materialize_controlled_run_plan(valid_payload()).canonical_bytes())
    changed = valid_payload()
    execution = changed["execution"]
    assert isinstance(execution, dict)
    execution["max_total_wall_time_seconds"] = 43199
    candidate.write_bytes(materialize_controlled_run_plan(changed).canonical_bytes())
    install_file_replacement(
        monkeypatch, target=target, candidate=candidate, replacement=replacement
    )

    with pytest.raises(ContractError, match="changed before snapshot"):
        ControlledRunPlanV2.from_file(target)


def test_v2_plan_file_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    linked = tmp_path / "linked.json"
    source.write_bytes(materialize_controlled_run_plan(valid_payload()).canonical_bytes())
    linked.symlink_to(source)

    with pytest.raises(ContractError, match="ordinary file"):
        ControlledRunPlanV2.from_file(linked)
