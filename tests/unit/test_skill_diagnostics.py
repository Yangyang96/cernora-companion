from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cernora import reload_batch_summary_package, reload_comparison_package

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.skill_capture.comparison import (
    ComparisonProfile,
    SkillComparisonPlan,
    compare_exports,
    freeze_comparison,
)
from cernora_reference_workflow.skill_capture.contracts import SkillPlan
from cernora_reference_workflow.skill_capture.diagnostics import diagnose_export
from cernora_reference_workflow.skill_capture.runtime import publish_export
from tests.unit.test_skill_capture import make_plan, native


@pytest.fixture
def plan() -> SkillPlan:
    return make_plan()


@pytest.mark.parametrize(
    "variant,code",
    [
        ("wrong", "fact_mismatch"),
        ("no_skill", "loading_unobserved"),
        ("missing_usage", "usage_incomplete"),
        ("failed_tool", "tool_error"),
        ("missing", "completion_unavailable"),
    ],
)
def test_diagnostics_trace_to_native_without_changing_score(
    tmp_path: Path, plan: SkillPlan, variant: str, code: str
) -> None:
    source = tmp_path / "source"
    publish_export(source, native(plan, variant), plan)
    report = diagnose_export(plan, source, tmp_path / "report")
    assert code in [f["code"] for f in report["findings"]]
    assert all(
        f["source"]["sha256"] and f["causal_status"] == "not_established"
        for f in report["findings"]
    )
    if variant in ("no_skill", "missing_usage"):
        assert report["summary"]["outcome"] == "pass"


def fixture_comparison(
    tmp_path: Path,
    plan: SkillPlan,
    before: str | list[str],
    after: str | list[str],
    *,
    timeout: int = 180,
    threshold: int = 1000,
) -> tuple[Path, Path, SkillComparisonPlan]:
    left = [before] if isinstance(before, str) else before
    right = [after] if isinstance(after, str) else after
    assert len(left) == len(right)
    payload = plan.model_dump(mode="json")
    payload["system"] += " Return exact JSON."
    payload["timeout_seconds"] = timeout
    candidate = SkillPlan.model_validate_json(json.dumps(payload))
    comparison = SkillComparisonPlan.model_validate_json(
        json.dumps(
            dict(
                schema_version="cernora.reference.skill-comparison-plan/v1",
                baseline=plan.model_dump(mode="json"),
                candidate=candidate.model_dump(mode="json"),
                repetitions=len(left),
                purpose="synthetic_validation",
                practical_threshold_basis_points=threshold,
            )
        )
    )
    frozen = freeze_comparison(comparison)
    f = tmp_path / "freeze.json"
    f.write_bytes(canonical_json_bytes(frozen))
    sources = {}
    for index, slot in enumerate(frozen["slots"]):
        p = comparison.baseline if index % 2 == 0 else comparison.candidate
        source = tmp_path / f"source-{index}"
        variant = (left if index % 2 == 0 else right)[slot["repetition"] - 1]
        files = native(p, variant)
        files["events.jsonl"] = (
            canonical_json_bytes({"type": "session", "id": f"synthetic-{index}"})
            + b"\n"
            + files["events.jsonl"]
        )
        publish_export(source, files, p)
        sources[slot["trial_slot_id"]] = str(source)
    s = tmp_path / "sources.json"
    s.write_text(json.dumps(sources))
    return f, s, comparison


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ("wrong", "pass", "improved"),
        ("pass", "wrong", "regressed"),
        ("pass", "pass", "uncertain"),
        ("missing", "pass", "improved"),
    ],
)
def test_core_batch_comparison_and_strict_reload(
    tmp_path: Path, plan: SkillPlan, before: str, after: str, expected: str
) -> None:
    f, s, p = fixture_comparison(tmp_path, plan, before, after)
    assert ComparisonProfile(p, p.baseline).authority == ComparisonProfile(p, p.candidate).authority
    r = compare_exports(f, s, tmp_path / "report")
    core = reload_comparison_package(tmp_path / "report/comparison")
    batch = reload_batch_summary_package(tmp_path / "report/batch")
    assert r["conclusion"] == core.summary.conclusion
    assert r["batch_summary"] == batch.summary.model_dump(mode="json")
    assert core.summary.conclusion == expected


def test_timeout_drift_is_core_not_comparable(tmp_path: Path, plan: SkillPlan) -> None:
    f, s, _ = fixture_comparison(tmp_path, plan, "pass", "pass", timeout=179)
    assert compare_exports(f, s, tmp_path / "report")["conclusion"] == "not_comparable"


def test_reject_incomplete_or_substituted_matrix(tmp_path: Path, plan: SkillPlan) -> None:
    f, s, _ = fixture_comparison(tmp_path, plan, "pass", "pass")
    values = json.loads(s.read_text())
    values.pop(next(iter(values)))
    s.write_text(json.dumps(values))
    with pytest.raises(ValueError, match="exactly every"):
        compare_exports(f, s, tmp_path / "report")
    assert not (tmp_path / "report").exists()


def test_plan_reference_change_is_rejected(plan: SkillPlan) -> None:
    p: dict[str, Any] = plan.model_dump(mode="json")
    p["task"] = "Another task"
    with pytest.raises(ValueError, match="same task"):
        SkillComparisonPlan.model_validate_json(
            json.dumps(
                dict(
                    schema_version="cernora.reference.skill-comparison-plan/v1",
                    baseline=plan.model_dump(mode="json"),
                    candidate=p,
                    repetitions=1,
                    purpose="synthetic_validation",
                    practical_threshold_basis_points=0,
                )
            )
        )


@pytest.mark.parametrize(
    "left,right,threshold,expected",
    [
        (["wrong", "wrong"], ["pass", "missing"], 1000, "mixed"),
        (["wrong", "pass"], ["pass", "pass"], 7500, "no_change"),
    ],
)
def test_core_mixed_and_practical_no_change(
    tmp_path: Path,
    plan: SkillPlan,
    left: list[str],
    right: list[str],
    threshold: int,
    expected: str,
) -> None:
    f, s, _ = fixture_comparison(tmp_path, plan, left, right, threshold=threshold)
    assert compare_exports(f, s, tmp_path / "report")["conclusion"] == expected


def test_repeated_native_attempt_cannot_become_independent_trials(
    tmp_path: Path, plan: SkillPlan
) -> None:
    f, s, _ = fixture_comparison(tmp_path, plan, ["pass", "pass"], ["pass", "pass"])
    sources = json.loads(s.read_text())
    keys = list(sources)
    sources[keys[2]] = sources[keys[0]]
    s.write_text(json.dumps(sources))
    with pytest.raises(ValueError, match="multiple Trial"):
        compare_exports(f, s, tmp_path / "report")
    assert not (tmp_path / "report").exists()


def test_freeze_tamper_does_not_publish(tmp_path: Path, plan: SkillPlan) -> None:
    f, s, _ = fixture_comparison(tmp_path, plan, "pass", "pass")
    payload = json.loads(f.read_text())
    payload["slots"][0]["experiment_id"] = "a" * 64
    f.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="freeze authority"):
        compare_exports(f, s, tmp_path / "report")
    assert not (tmp_path / "report").exists()


def test_repeat_comparison_preserves_authoritative_bytes(tmp_path: Path, plan: SkillPlan) -> None:
    f, s, _ = fixture_comparison(tmp_path, plan, "wrong", "pass")
    compare_exports(f, s, tmp_path / "first")
    compare_exports(f, s, tmp_path / "second")
    for package in ("batch", "comparison"):
        one = {p.name: p.read_bytes() for p in (tmp_path / "first" / package).iterdir()}
        two = {p.name: p.read_bytes() for p in (tmp_path / "second" / package).iterdir()}
        assert one == two
