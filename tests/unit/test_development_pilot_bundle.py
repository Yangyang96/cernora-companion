from __future__ import annotations

import importlib.metadata
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    sha256_bytes,
)
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
    verify_development_pilot_runtime,
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
        build_base_image="cernora-reference/pi-runtime@sha256:" + "a" * 64,
        images=images,
    )
    path = tmp_path / "images.json"
    path.write_bytes(authority.canonical_bytes())
    return path


def _reindex_bundle_file(destination: Path, relative: str, data: bytes) -> None:
    (destination / relative).write_bytes(data)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    indexed = next(item for item in manifest["files"] if item["path"] == relative)
    indexed["size"] = len(data)
    indexed["sha256"] = sha256_bytes(data)
    manifest.pop("bundle_id")
    manifest["bundle_id"] = canonical_content_id(manifest, excluded=frozenset())
    manifest_path.write_bytes(canonical_json_bytes(manifest))


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
        repository_root=tmp_path,
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
    assert request.planned_trial_count == 9
    assert request.maximum_attempt_count == 18
    assert request.per_attempt_timeout_seconds == 600
    assert request.schema_version == "cernora.reference.development-pilot-authorization-request/v3"
    assert request.attempt_envelope_timeout_seconds == 660
    expected_custody = (
        tmp_path.resolve(strict=True)
        / ".agent"
        / "custody"
        / f"development-pilot-{request.plan_id}"
    )
    assert request.custody_path_sha256 == sha256_bytes(os.fsencode(expected_custody))
    assert request.proxy_sources == (
        "CERNORA_HTTP_PROXY",
        "CERNORA_HTTPS_PROXY",
        "CERNORA_ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    )
    assert request.maximum_wall_seconds == 14400
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
    assert plan.schema_version == "cernora.reference.development-agent-pilot-plan/v4"
    assert plan.implementation_candidates == created.implementation_candidates
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
        repository_root=tmp_path,
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


def test_bundle_rejects_self_consistent_request_for_different_case_authority(
    tmp_path: Path,
) -> None:
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
        repository_root=tmp_path,
    )
    request_path = destination / "authorization-request.json"
    request = json.loads(request_path.read_bytes())
    previous_request_id = request["request_id"]
    request["case_authority_sha256"][0] = "d" * 64
    request.pop("request_id")
    request["request_id"] = canonical_content_id(request, excluded=frozenset())
    request_bytes = canonical_json_bytes(request)
    request_path.write_bytes(request_bytes)
    review_path = destination / "review.md"
    review_bytes = review_path.read_bytes().replace(
        previous_request_id.encode("ascii"), request["request_id"].encode("ascii")
    )
    review_path.write_bytes(review_bytes)

    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    request_file = next(
        item for item in manifest["files"] if item["path"] == "authorization-request.json"
    )
    request_file["size"] = len(request_bytes)
    request_file["sha256"] = sha256_bytes(request_bytes)
    review_file = next(item for item in manifest["files"] if item["path"] == "review.md")
    review_file["size"] = len(review_bytes)
    review_file["sha256"] = sha256_bytes(review_bytes)
    manifest["authorization_request_id"] = request["request_id"]
    manifest.pop("bundle_id")
    manifest["bundle_id"] = canonical_content_id(manifest, excluded=frozenset())
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(ContractError, match="authorities do not close"):
        inspect_development_pilot_bundle(destination)


@pytest.mark.parametrize(
    ("relative", "mutation", "message"),
    (
        ("review.md", b"\nAuthorization expanded.\n", "review does not equal"),
        ("corpus.json", b"\n", "corpus is not canonical"),
    ),
)
def test_bundle_rejects_self_consistent_review_or_corpus_rewrite(
    tmp_path: Path,
    relative: str,
    mutation: bytes,
    message: str,
) -> None:
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
        repository_root=tmp_path,
    )
    original = (destination / relative).read_bytes()
    _reindex_bundle_file(destination, relative, original + mutation)

    with pytest.raises(ContractError, match=message):
        inspect_development_pilot_bundle(destination)


def test_historical_unbound_bundle_is_frozen_codex_era_evidence() -> None:
    """The era boundary freezes the historical v1 bundle; current contracts reject it."""

    historical = ROOT / "preparations" / "next-priority4-development-pilot"
    assert (historical / "plan.json").is_file(), "historical evidence removed"

    with pytest.raises(ValidationError, match="cernora-reference-harbor-pi"):
        inspect_development_pilot_bundle(historical)


def test_historical_v2_recovery_is_frozen_codex_era_evidence() -> None:
    """The era boundary freezes the historical v2 bundle; current contracts reject it."""

    historical = ROOT / "preparations" / "next-priority4-development-pilot-recovery"
    assert (historical / "plan.json").is_file(), "historical evidence removed"

    with pytest.raises(ValidationError, match="cernora-reference-harbor-pi"):
        inspect_development_pilot_bundle(historical)


def test_runtime_attestation_binds_active_venv_to_exact_wheels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        repository_root=tmp_path,
    )
    plan = DevelopmentAgentPilotPlan.from_file(destination / "plan.json")
    repository = tmp_path / "runtime"
    prefix = repository / ".venv"
    installed = prefix / "site-packages"
    installed.mkdir(parents=True)
    for wheel in (companion, cernora):
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(installed)

    class Distribution:
        def __init__(self, version: str) -> None:
            self.version = version

        def locate_file(self, path: str) -> Path:
            return installed / path

    versions = {"cernora": "0.1.4", "cernora-reference-workflow": "0.4.0"}
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(
        importlib.metadata,
        "distribution",
        lambda name: Distribution(versions[name]),
    )

    verify_development_pilot_runtime(
        plan,
        repository_root=repository,
        companion_wheel=companion,
        cernora_wheel=cernora,
    )

    changed = next(installed.glob("cernora_reference_workflow*/__init__.py"))
    changed.write_bytes(changed.read_bytes() + b"drift")
    with pytest.raises(ContractError, match="distribution bytes changed"):
        verify_development_pilot_runtime(
            plan,
            repository_root=repository,
            companion_wheel=companion,
            cernora_wheel=cernora,
        )
