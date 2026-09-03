from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.run_plan import RunPlan, materialize_run_plan

ROOT = Path(__file__).resolve().parents[2]


def valid_payload() -> dict[str, object]:
    first = ExperimentSpec.from_file(ROOT / "examples/tiny-calculator-v1.json")
    second = ExperimentSpec.from_file(ROOT / "examples/tiny-calculator-v2.json")
    cells = [
        {
            "case_id": first.task.task_id,
            "configuration_id": configuration,
            "experiment_id": first.experiment_id,
        }
        for configuration in ("baseline", "candidate")
    ] + [
        {
            "case_id": second.task.task_id,
            "configuration_id": configuration,
            "experiment_id": second.experiment_id,
        }
        for configuration in ("baseline", "candidate")
    ]
    return {
        "schema_version": "cernora.reference.run-plan/v1",
        "companion_version": "0.2.0",
        "cernora_version": "0.1.2",
        "connector": {
            "connector_id": "cernora-reference-harbor-pi",
            "connector_version": "2",
            "platform_qualification": "macos-arm64",
        },
        "experiment_specs": [
            first.model_dump(mode="json"),
            second.model_dump(mode="json"),
        ],
        "cases": [
            {
                "case_id": spec.task.task_id,
                "case_version": spec.task.task_version,
                "task_content_sha256": spec.task.content_sha256,
            }
            for spec in (first, second)
        ],
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
            "max_total_wall_time_seconds": 86400,
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
            "method": "none",
            "method_version": "m1",
            "aggregate_quality_conclusion": False,
        },
    }


def test_run_plan_expands_exact_ordered_twelve_slot_matrix() -> None:
    plan = materialize_run_plan(valid_payload())
    slots = plan.expand_trial_slots()

    assert len(slots) == 12
    assert tuple(slot.slot_index for slot in slots) == tuple(range(1, 13))
    assert tuple(slot.repetition for slot in slots) == (1, 2, 3) * 4
    assert len({slot.trial_slot_id for slot in slots}) == 12
    assert all(slot.run_plan_id == plan.run_plan_id for slot in slots)
    assert plan == materialize_run_plan(deepcopy(valid_payload()))


def test_behavior_affecting_budget_changes_plan_and_slot_identity() -> None:
    first = materialize_run_plan(valid_payload())
    changed = valid_payload()
    assert isinstance(changed["execution"], dict)
    changed["execution"]["max_attempt_count"] = 12
    second = materialize_run_plan(changed)

    assert first.run_plan_id != second.run_plan_id
    assert (
        first.expand_trial_slots()[0].trial_slot_id != second.expand_trial_slots()[0].trial_slot_id
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown",
        "duplicate_cell",
        "unknown_case",
        "mismatched_case",
        "unreferenced_spec",
        "wrong_trial_count",
        "invalid_attempt_budget",
    ),
)
def test_run_plan_rejects_malformed_or_incomplete_matrix(mutation: str) -> None:
    payload = valid_payload()
    if mutation == "unknown":
        payload["created_at"] = "2026-08-26T00:00:00Z"
    elif mutation == "duplicate_cell":
        assert isinstance(payload["cells"], list)
        payload["cells"][1] = deepcopy(payload["cells"][0])
    elif mutation == "unknown_case":
        assert isinstance(payload["cells"], list)
        payload["cells"][0]["case_id"] = "unknown"
    elif mutation == "mismatched_case":
        assert isinstance(payload["cells"], list)
        payload["cells"][0]["experiment_id"] = payload["cells"][2]["experiment_id"]
    elif mutation == "unreferenced_spec":
        assert isinstance(payload["cells"], list)
        payload["cells"] = payload["cells"][:2]
        payload["planned_trial_count"] = 6
        payload["worst_case_attempt_count"] = 12
        assert isinstance(payload["execution"], dict)
        payload["execution"]["max_attempt_count"] = 12
    elif mutation == "wrong_trial_count":
        payload["planned_trial_count"] = 11
    else:
        assert isinstance(payload["execution"], dict)
        payload["execution"]["max_attempt_count"] = 25
    with pytest.raises((ContractError, ValidationError, ValueError)):
        materialize_run_plan(payload)


def test_run_plan_file_requires_canonical_bytes(tmp_path: Path) -> None:
    plan = materialize_run_plan(valid_payload())
    canonical = tmp_path / "run-plan.json"
    canonical.write_bytes(plan.canonical_bytes())
    assert RunPlan.from_file(canonical) == plan

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text("{\n" + plan.canonical_bytes().decode()[1:], encoding="utf-8")
    with pytest.raises(ContractError, match="not canonical"):
        RunPlan.from_file(noncanonical)
