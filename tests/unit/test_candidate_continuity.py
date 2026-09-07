from __future__ import annotations

import hashlib
from pathlib import Path

from cernora_reference_workflow.candidate_development import (
    CandidateDevelopmentRecord,
    candidate_continuity_violations,
    freeze_candidate_development,
)
from cernora_reference_workflow.common import (
    canonical_json_bytes,
    load_json_bytes,
    read_regular_file_bytes,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    CanonicalAuthoritySource,
)

REPOSITORY = Path(__file__).resolve().parents[2]
WORKSHEET = REPOSITORY / "preparations" / "p4-candidate-development-csv-quoted"


def _committed_record() -> CandidateDevelopmentRecord:
    raw = read_regular_file_bytes(WORKSHEET / "record.json")
    payload = load_json_bytes(raw)
    assert isinstance(payload, dict)
    record = CandidateDevelopmentRecord.model_validate(payload)
    assert candidate_canonical_bytes(record) == raw
    return record


def candidate_canonical_bytes(record: CandidateDevelopmentRecord) -> bytes:
    return canonical_json_bytes(record.model_dump(mode="json"))


def _remint_payload(record: CandidateDevelopmentRecord) -> dict[str, object]:
    payload: dict[str, object] = dict(record.model_dump(mode="json"))
    payload.pop("development_id")
    return payload


def test_committed_worksheet_binds_treatment_payload_bytes() -> None:
    record = _committed_record()

    raw = read_regular_file_bytes(WORKSHEET / "candidate-prompt.json")
    payload = load_json_bytes(raw)
    assert isinstance(payload, dict)
    source = CanonicalAuthoritySource.model_validate(payload)
    assert canonical_json_bytes(source.model_dump(mode="json")) == raw

    assert source.source_id == "p4-confirmatory-candidate-prompt-v1"
    assert source.source_sha256 == record.candidate.treatment_sha256
    selected = source.payload
    assert isinstance(selected, dict)
    binding = selected["selected_failure"]
    assert isinstance(binding, dict)
    assert binding["code"] == record.hypothesis.observed_failure_code


def test_case_set_remint_is_continuous() -> None:
    prior = _committed_record()

    payload = _remint_payload(prior)
    new_baseline = hashlib.sha256(b"final-study-case-set-baseline").hexdigest()
    new_candidate = hashlib.sha256(b"final-study-case-set-candidate").hexdigest()
    baseline = payload["baseline"]
    candidate = payload["candidate"]
    assert isinstance(baseline, dict) and isinstance(candidate, dict)
    baseline["authority_sha256"] = new_baseline
    candidate["baseline_authority_sha256"] = new_baseline
    candidate["authority_sha256"] = new_candidate

    remint = freeze_candidate_development(payload)
    assert remint.development_id != prior.development_id
    assert candidate_continuity_violations(prior, remint) == ()


def test_treatment_digest_drift_is_reported() -> None:
    prior = _committed_record()

    payload = _remint_payload(prior)
    candidate = payload["candidate"]
    assert isinstance(candidate, dict)
    candidate["authority_sha256"] = hashlib.sha256(b"final-study-case-set").hexdigest()
    candidate["treatment_sha256"] = hashlib.sha256(b"drifted-treatment").hexdigest()
    remint = freeze_candidate_development(payload)

    violations = candidate_continuity_violations(prior, remint)
    assert any("treatment digest" in item for item in violations)


def test_hypothesis_drift_is_reported() -> None:
    prior = _committed_record()

    payload = _remint_payload(prior)
    hypothesis = payload["hypothesis"]
    assert isinstance(hypothesis, dict)
    hypothesis["mechanism"] = "A drifted mechanism claim."
    remint = freeze_candidate_development(payload)

    violations = candidate_continuity_violations(prior, remint)
    assert any("hypothesis" in item for item in violations)


def test_observation_drift_is_reported() -> None:
    prior = _committed_record()

    payload = _remint_payload(prior)
    observations = payload["observations"]
    assert isinstance(observations, list)
    payload["observations"] = observations[:-1]
    remint = freeze_candidate_development(payload)

    violations = candidate_continuity_violations(prior, remint)
    assert any("observations" in item for item in violations)
