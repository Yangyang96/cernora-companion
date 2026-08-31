from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.development_agent_pilot import (
    PILOT_CASE_IDS,
    DevelopmentAgentPilotPlan,
    build_development_agent_pilot_plan,
    load_development_pilot_corpus,
    materialize_development_pilot_image_set,
)
from cernora_reference_workflow.study_preparation import ImplementationCandidate

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "examples" / "priority4-development-pilot"
BASE = "cernora-reference/codex-runtime@sha256:" + "a" * 64


def _implementations() -> tuple[ImplementationCandidate, ...]:
    return (
        ImplementationCandidate(
            name="cernora", version="0.1.4", kind="wheel", size=1, sha256="b" * 64
        ),
        ImplementationCandidate(
            name="cernora-reference-workflow",
            version="0.4.0",
            kind="wheel",
            size=1,
            sha256="c" * 64,
        ),
    )


def _images() -> dict[str, str]:
    return {
        case_id: f"cernora-reference/p4-pilot-{case_id}@sha256:{index:064x}"
        for index, case_id in enumerate(PILOT_CASE_IDS, start=1)
    }


def test_fresh_corpus_is_offline_calibrated_without_agent_observations() -> None:
    corpus = load_development_pilot_corpus(CORPUS)

    assert tuple(item.case.case_id for item in corpus.tasks) == PILOT_CASE_IDS
    assert [item.split_id for item in corpus.tasks].count("development") == 3
    assert [item.split_id for item in corpus.tasks].count("regression") == 3
    assert all(item.baseline_exit_code > 0 for item in corpus.calibrations)
    assert all(item.solution_exit_code == 0 for item in corpus.calibrations)
    assert all(item.source == "verifier-calibration" for item in corpus.calibrations)
    assert all(item.agent_outcome == "not-observed" for item in corpus.calibrations)
    assert corpus == load_development_pilot_corpus(CORPUS)
    assert corpus.canonical_bytes() == load_development_pilot_corpus(CORPUS).canonical_bytes()


def test_fresh_corpus_accepts_a_relative_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)

    corpus = load_development_pilot_corpus(Path("examples/priority4-development-pilot"))

    assert tuple(item.case.case_id for item in corpus.tasks) == PILOT_CASE_IDS


def test_confirmatory_pilot_plan_is_exact_baseline_only_and_not_authorized() -> None:
    corpus = load_development_pilot_corpus(CORPUS)
    image_set = materialize_development_pilot_image_set(
        build_base_image=BASE,
        images=_images(),
    )

    plan = build_development_agent_pilot_plan(
        corpus=corpus,
        images=image_set,
        implementation_candidates=_implementations(),
    )

    assert plan.selected_study_mode == "confirmatory-effect"
    assert plan.execution_authorized is False
    assert plan.implementation_candidates == _implementations()
    assert plan.treatment_axis_if_eligible == "prompt-instruction"
    assert plan.planned_trial_count == 6
    assert plan.worst_case_attempt_count == 12
    assert plan.execution.max_attempt_count == 12
    assert plan.execution.max_total_wall_time_seconds == 7200
    assert {item.configuration_id for item in plan.experiment_specs} == {"baseline"}
    assert {item.runtime.model for item in plan.experiment_specs} == {"gpt-5.6-terra"}
    assert {item.runtime.reasoning_effort for item in plan.experiment_specs} == {"medium"}
    assert {item.limits.timeout_seconds for item in plan.experiment_specs} == {300}
    assert tuple(item.task.task_id for item in plan.experiment_specs) == PILOT_CASE_IDS
    assert "held-out-access" in plan.prohibited_actions
    assert "54-trial-matrix" in plan.prohibited_actions
    assert DevelopmentAgentPilotPlan.from_bytes(plan.canonical_bytes()) == plan


def test_corpus_rejects_extra_or_relabelled_cases(tmp_path: Path) -> None:
    copied = tmp_path / "corpus"
    shutil.copytree(CORPUS, copied)
    shutil.copytree(copied / "dev-json-pointer", copied / "extra")
    with pytest.raises(ContractError, match="closed six-directory"):
        load_development_pilot_corpus(copied)

    shutil.rmtree(copied / "extra")
    extra_link = copied / "extra-link"
    extra_link.symlink_to(copied / "dev-json-pointer", target_is_directory=True)
    with pytest.raises(ContractError, match="closed six-directory"):
        load_development_pilot_corpus(copied)

    extra_link.unlink()
    manifest = copied / "dev-json-pointer" / "case.json"
    raw = manifest.read_text(encoding="utf-8").replace('"development"', '"held-out"')
    manifest.write_text(raw, encoding="utf-8")
    with pytest.raises(ContractError, match="fresh and exact"):
        load_development_pilot_corpus(copied)


def test_plan_rejects_image_or_authorization_drift() -> None:
    corpus = load_development_pilot_corpus(CORPUS)
    image_set = materialize_development_pilot_image_set(
        build_base_image=BASE,
        images=_images(),
    )
    plan = build_development_agent_pilot_plan(
        corpus=corpus,
        images=image_set,
        implementation_candidates=_implementations(),
    )
    payload = plan.model_dump(mode="json")
    payload["execution_authorized"] = True

    with pytest.raises(ValidationError):
        DevelopmentAgentPilotPlan.model_validate(payload)

    payload = plan.model_dump(mode="json")
    payload["implementation_candidates"][1]["sha256"] = "d" * 64
    with pytest.raises(ValidationError):
        DevelopmentAgentPilotPlan.model_validate(payload)

    wrong = _images()
    wrong.pop(PILOT_CASE_IDS[-1])
    with pytest.raises(ContractError, match="fresh Case set"):
        materialize_development_pilot_image_set(build_base_image=BASE, images=wrong)
