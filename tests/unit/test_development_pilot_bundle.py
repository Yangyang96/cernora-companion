from __future__ import annotations

import json
from pathlib import Path

import pytest

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.development_agent_pilot import (
    PILOT_CASE_IDS,
    DevelopmentAgentPilotPlan,
    DevelopmentPilotImageSet,
    materialize_development_pilot_image_set,
)
from cernora_reference_workflow.development_pilot_bundle import (
    DevelopmentPilotAuthorizationRequest,
    create_development_pilot_bundle,
    inspect_development_pilot_bundle,
    verify_development_pilot_bundle,
)
from tests.unit.test_study_preparation import _candidate_wheels

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "examples" / "priority4-development-pilot"


def _image_authorities(tmp_path: Path) -> Path:
    images = {
        case_id: f"cernora-reference/p4-pilot-{case_id}@sha256:{index:064x}"
        for index, case_id in enumerate(PILOT_CASE_IDS, start=1)
    }
    authority = materialize_development_pilot_image_set(
        build_base_image="cernora-reference/codex-runtime@sha256:" + "a" * 64,
        images=images,
    )
    path = tmp_path / "images.json"
    path.write_bytes(authority.canonical_bytes())
    return path


def test_bundle_closes_exact_unapproved_development_request(tmp_path: Path) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    destination = tmp_path / "bundle"
    created = create_development_pilot_bundle(
        destination,
        corpus_root=CORPUS,
        image_authorities=_image_authorities(tmp_path),
        companion_wheel=companion,
        cernora_wheel=cernora,
        companion_version="0.4.0",
        cernora_version="0.1.4",
    )

    assert inspect_development_pilot_bundle(destination) == created
    assert (
        verify_development_pilot_bundle(
            destination,
            companion_wheel=companion,
            cernora_wheel=cernora,
        )
        == created
    )
    assert created.status == "awaiting-development-pilot-authorization"
    assert created.selected_study_mode == "confirmatory-effect"
    assert created.execution_authorized is False
    request = DevelopmentPilotAuthorizationRequest.model_validate_json(
        (destination / "authorization-request.json").read_bytes()
    )
    assert request.plan_id == created.plan_id
    assert request.planned_trial_count == 6
    assert request.maximum_attempt_count == 12
    assert request.per_attempt_timeout_seconds == 300
    assert request.maximum_wall_seconds == 7200
    assert request.completion_stop == "before-candidate-construction"
    assert request.no_failure_stop == "no-candidate"
    assert request.missing_evidence_stop == "inconclusive"
    assert "held-out-reveal" in request.explicitly_not_authorized
    assert "54-trial-matrix" in request.explicitly_not_authorized
    assert set(path.name for path in destination.iterdir()) == {
        "authorization-request.json",
        "corpus.json",
        "images.json",
        "manifest.json",
        "plan.json",
        "review.md",
    }
    serialized = b"".join(
        path.read_bytes() for path in sorted(destination.iterdir()) if path.is_file()
    )
    assert b'"agent_outcome":"behavioral-failure"' not in serialized
    plan = DevelopmentAgentPilotPlan.from_file(destination / "plan.json")
    assert {item.configuration_id for item in plan.experiment_specs} == {"baseline"}
    DevelopmentPilotImageSet.from_file(destination / "images.json")


def test_bundle_rejects_tampering_and_changed_wheel(tmp_path: Path) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    destination = tmp_path / "bundle"
    create_development_pilot_bundle(
        destination,
        corpus_root=CORPUS,
        image_authorities=_image_authorities(tmp_path),
        companion_wheel=companion,
        cernora_wheel=cernora,
        companion_version="0.4.0",
        cernora_version="0.1.4",
    )
    companion.write_bytes(companion.read_bytes() + b"tamper")
    with pytest.raises(ContractError, match="implementation candidate"):
        verify_development_pilot_bundle(
            destination,
            companion_wheel=companion,
            cernora_wheel=cernora,
        )

    payload = json.loads((destination / "authorization-request.json").read_bytes())
    payload["maximum_attempt_count"] = 13
    (destination / "authorization-request.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ContractError, match="bundle file"):
        inspect_development_pilot_bundle(destination)
