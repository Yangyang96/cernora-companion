from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.candidate_development import (
    freeze_candidate_development,
)


def candidate_development_payload() -> dict[str, object]:
    return {
        "schema_version": "cernora.reference.candidate-development/v1",
        "baseline": {
            "configuration_id": "baseline",
            "authority_sha256": "a" * 64,
        },
        "candidate": {
            "configuration_id": "candidate",
            "baseline_authority_sha256": "a" * 64,
            "authority_sha256": "b" * 64,
            "treatment_axis": "prompt-instruction",
            "treatment_sha256": "c" * 64,
        },
        "hypothesis": {
            "observed_failure_code": "interval-boundary-v1",
            "mechanism": "The agent misses inclusive endpoint overlap.",
            "intervention_scope": "Prompt guidance for interval repair reasoning.",
            "expected_observation": "Fewer boundary failures on unseen interval tasks.",
            "falsifier": "No held-out boundary improvement or a protected-path regression.",
        },
        "observations": [
            {
                "observation_id": "agent-failure-001",
                "case_id": "dev-ledger",
                "split": "development",
                "source": "agent-pilot",
                "agent_outcome": "behavioral-failure",
                "failure_code": "interval-boundary-v1",
                "evidence_sha256": "d" * 64,
            },
            {
                "observation_id": "verifier-calibration-001",
                "case_id": "reg-query",
                "split": "regression",
                "source": "verifier-calibration",
                "agent_outcome": "not-observed",
                "failure_code": None,
                "evidence_sha256": "e" * 64,
            },
        ],
    }


def test_candidate_development_freezes_one_patch_over_baseline() -> None:
    frozen = freeze_candidate_development(candidate_development_payload())

    assert frozen.candidate.baseline_authority_sha256 == frozen.baseline.authority_sha256
    assert frozen.hypothesis.observed_failure_code == "interval-boundary-v1"
    assert frozen.observations[0].source == "agent-pilot"
    assert len(frozen.development_id) == 64


def test_verifier_calibration_cannot_stand_in_for_an_agent_pilot() -> None:
    payload = candidate_development_payload()
    assert isinstance(payload["observations"], list)
    payload["observations"] = payload["observations"][1:]

    with pytest.raises(ValidationError, match="Agent failure"):
        freeze_candidate_development(payload)


def test_candidate_development_rejects_heldout_observations() -> None:
    payload = deepcopy(candidate_development_payload())
    assert isinstance(payload["observations"], list)
    assert isinstance(payload["observations"][0], dict)
    payload["observations"][0]["split"] = "held-out"

    with pytest.raises(ValidationError):
        freeze_candidate_development(payload)
