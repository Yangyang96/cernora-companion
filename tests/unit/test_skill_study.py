"""Neutral synthetic protocol tests, not model-effect evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cernora import reload_comparison_package

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.skill_capture.comparison import (
    ComparisonProfile,
    SkillStudyPlan,
    compare_exports,
    freeze_comparison,
)
from cernora_reference_workflow.skill_capture.runtime import publish_export
from tests.unit.test_skill_capture import make_plan, native


def study() -> SkillStudyPlan:
    cases = []
    for index, split in enumerate(("development", "workflow_check")):
        raw = (
            make_plan()
            .model_dump_json()
            .replace("leaf", f"leaf{index}")
            .replace("root", f"root{index}")
        )
        baseline = json.loads(raw)
        baseline["case_id"] = f"case-{index}"
        candidate = json.loads(json.dumps(baseline))
        candidate["skill_files"]["SKILL.md"] += "Return JSON without fences.\n"
        cases.append(dict(split=split, baseline=baseline, candidate=candidate))
    return SkillStudyPlan.model_validate_json(
        json.dumps(
            dict(
                schema_version="cernora.reference.skill-comparison-plan/v2",
                cases=cases,
                repetitions=1,
                purpose="synthetic_validation",
                practical_threshold_basis_points=0,
                primary_split="workflow_check",
            )
        )
    )


@pytest.mark.parametrize(
    "development,check,expected",
    [
        (("wrong", "pass"), ("pass", "pass"), "uncertain"),
        (("pass", "pass"), ("wrong", "pass"), "improved"),
        (("pass", "wrong"), ("wrong", "pass"), "mixed"),
    ],
)
def test_split_primary_and_regression_guardrail(
    tmp_path: Path, development: tuple[str, str], check: tuple[str, str], expected: str
) -> None:
    plan = study()
    frozen = freeze_comparison(plan)
    assert [s["configuration_id"] for s in frozen["slots"]] == [
        "baseline",
        "candidate",
        "candidate",
        "baseline",
    ]
    assert (
        ComparisonProfile(plan, plan.cases[0].baseline).authority
        == ComparisonProfile(
            plan,
            plan.cases[1].candidate,
        ).authority
    )
    sources = {}
    for index, slot in enumerate(frozen["slots"]):
        case_index = int(slot["case_id"][-1])
        arm = slot["configuration_id"]
        selected = getattr(plan.cases[case_index], arm)
        variant = (development, check)[case_index][arm == "candidate"]
        files = native(selected, variant)
        # Distinct native attempts, preserving actual outcomes.
        files["events.jsonl"] = (
            canonical_json_bytes(dict(type="session", id=f"fixture-{index}"))
            + b"\n"
            + files["events.jsonl"]
        )
        target = tmp_path / str(index)
        publish_export(target, files, selected)
        sources[slot["trial_slot_id"]] = str(target)
    freeze_path = tmp_path / "freeze.json"
    sources_path = tmp_path / "sources.json"
    freeze_path.write_bytes(canonical_json_bytes(frozen))
    sources_path.write_bytes(canonical_json_bytes(sources))
    result = compare_exports(freeze_path, sources_path, tmp_path / "report")
    assert result["conclusion"] == expected
    assert reload_comparison_package(tmp_path / "report/comparison").summary.conclusion == expected


@pytest.mark.parametrize(
    "mutation,match",
    [("overlap", "related objects"), ("drift", "global"), ("reference", "preserve")],
)
def test_reject_study_drift(mutation: str, match: str) -> None:
    data = study().model_dump(mode="json")
    if mutation == "overlap":
        for arm in ("baseline", "candidate"):
            data["cases"][1][arm] = json.loads(json.dumps(data["cases"][0][arm]))
            data["cases"][1][arm]["case_id"] = "case-1"
    elif mutation == "drift":
        data["cases"][1]["candidate"]["timeout_seconds"] -= 1
    else:
        data["cases"][1]["candidate"]["task"] += " changed"
    with pytest.raises(ValueError, match=match):
        SkillStudyPlan.model_validate_json(json.dumps(data))
