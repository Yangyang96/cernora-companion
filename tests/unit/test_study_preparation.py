from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.study_preparation import (
    create_study_preparation_bundle,
    inspect_study_preparation_bundle,
    verify_study_preparation_bundle,
)


def _wheel(path: Path, *, distribution: str, version: str) -> Path:
    metadata_name = distribution.replace("-", "_")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{metadata_name}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: {distribution}\nVersion: {version}\n",
        )
        archive.writestr(f"{metadata_name}/__init__.py", "")
    return path


def _candidate_wheels(tmp_path: Path) -> tuple[Path, Path]:
    return (
        _wheel(
            tmp_path / "cernora_reference_workflow-0.4.0-py3-none-any.whl",
            distribution="cernora-reference-workflow",
            version="0.4.0",
        ),
        _wheel(
            tmp_path / "cernora-0.1.4-py3-none-any.whl",
            distribution="cernora",
            version="0.1.4",
        ),
    )


def test_new_preparation_bundle_stops_at_explicit_user_decisions(tmp_path: Path) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    destination = tmp_path / "next-study-preparation"

    created = create_study_preparation_bundle(
        destination,
        companion_wheel=companion,
        cernora_wheel=cernora,
        companion_version="0.4.0",
        cernora_version="0.1.4",
        preparation_nonce="a" * 64,
    )
    reloaded = inspect_study_preparation_bundle(destination)

    assert reloaded == created
    assert reloaded.status == "awaiting-user-decisions"
    assert reloaded.dossier_status == "draft"
    assert reloaded.design_proposal.status == "non-binding-proposal"
    assert reloaded.design_proposal.recommended_mode == "confirmatory-effect"
    assert reloaded.design_proposal.bounds.case_count == 9
    assert reloaded.design_proposal.bounds.configuration_count == 2
    assert reloaded.design_proposal.bounds.repetitions == 3
    assert reloaded.design_proposal.bounds.planned_trial_count == 54
    assert reloaded.design_proposal.bounds.maximum_attempt_count == 108
    assert reloaded.design_proposal.bounds.maximum_wall_seconds == 43_200
    assert reloaded.design_proposal.primary_scope == "held-out"
    assert {item.decision_id for item in reloaded.pending_decisions} >= {
        "scientific-question",
        "fresh-candidate",
        "fresh-heldout-custodian",
        "fresh-heldout-commitment",
        "implementation-lock",
        "study-administration",
        "study-design",
    }
    assert all(item.status == "pending" for item in reloaded.pending_decisions)
    assert tuple(item.name for item in reloaded.implementation_candidates) == (
        "cernora",
        "cernora-reference-workflow",
    )
    assert set(path.relative_to(destination).as_posix() for path in destination.rglob("*")) == {
        "analysis-policy-proposal.json",
        "decisions.json",
        "dossier.md",
        "manifest.json",
    }
    serialized = b"".join(
        path.read_bytes() for path in sorted(destination.rglob("*")) if path.is_file()
    )
    for forbidden in (
        b"reveal_id",
        b"acceptance_id",
        b"execution_nonce",
        b"run_plan_id",
        b"comparison_plan_id",
        b"start-execution",
        b"step-execution",
    ):
        assert forbidden not in serialized


def test_preparation_bundle_identity_is_fresh_and_source_bytes_are_bound(tmp_path: Path) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    first = create_study_preparation_bundle(
        tmp_path / "first",
        companion_wheel=companion,
        cernora_wheel=cernora,
        companion_version="0.4.0",
        cernora_version="0.1.4",
        preparation_nonce="1" * 64,
    )
    second = create_study_preparation_bundle(
        tmp_path / "second",
        companion_wheel=companion,
        cernora_wheel=cernora,
        companion_version="0.4.0",
        cernora_version="0.1.4",
        preparation_nonce="2" * 64,
    )

    assert first.preparation_id != second.preparation_id
    companion.write_bytes(companion.read_bytes() + b"tamper")
    with pytest.raises(ContractError, match="implementation candidate"):
        verify_study_preparation_bundle(
            tmp_path / "first",
            companion_wheel=companion,
            cernora_wheel=cernora,
        )


def test_preparation_bundle_strict_reload_rejects_tampering(tmp_path: Path) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    destination = tmp_path / "preparation"
    create_study_preparation_bundle(
        destination,
        companion_wheel=companion,
        cernora_wheel=cernora,
        companion_version="0.4.0",
        cernora_version="0.1.4",
        preparation_nonce="3" * 64,
    )
    (destination / "dossier.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(ContractError, match="bundle file"):
        inspect_study_preparation_bundle(destination)


def test_preparation_bundle_strict_reload_rejects_unknown_empty_directory(
    tmp_path: Path,
) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    destination = tmp_path / "preparation"
    create_study_preparation_bundle(
        destination,
        companion_wheel=companion,
        cernora_wheel=cernora,
        companion_version="0.4.0",
        cernora_version="0.1.4",
        preparation_nonce="4" * 64,
    )
    (destination / "unknown").mkdir()

    with pytest.raises(ContractError, match="unknown or missing"):
        inspect_study_preparation_bundle(destination)


def test_preparation_bundle_rejects_reauthored_analysis_policy_proposal(
    tmp_path: Path,
) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    destination = tmp_path / "preparation"
    create_study_preparation_bundle(
        destination,
        companion_wheel=companion,
        cernora_wheel=cernora,
        companion_version="0.4.0",
        cernora_version="0.1.4",
        preparation_nonce="5" * 64,
    )
    proposal_path = destination / "analysis-policy-proposal.json"
    changed = proposal_path.read_bytes().replace(b'"0.95"', b'"0.90"')
    proposal_path.write_bytes(changed)
    manifest_path = destination / "manifest.json"
    payload = json.loads(manifest_path.read_bytes())
    proposal_record = next(
        item for item in payload["files"] if item["path"] == "analysis-policy-proposal.json"
    )
    proposal_record["size"] = len(changed)
    proposal_record["sha256"] = sha256_bytes(changed)
    payload_without_id = {key: value for key, value in payload.items() if key != "preparation_id"}
    payload["preparation_id"] = canonical_content_id(payload_without_id, excluded=frozenset())
    manifest_path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises((ContractError, ValueError), match="analysis policy"):
        inspect_study_preparation_bundle(destination)


def test_preparation_bundle_rejects_wrong_implementation_candidate(tmp_path: Path) -> None:
    companion = _wheel(
        tmp_path / "cernora_reference_workflow-0.3.0-py3-none-any.whl",
        distribution="cernora-reference-workflow",
        version="0.3.0",
    )
    cernora = _wheel(
        tmp_path / "cernora-0.1.4-py3-none-any.whl",
        distribution="cernora",
        version="0.1.4",
    )

    with pytest.raises(ContractError, match="wheel identity"):
        create_study_preparation_bundle(
            tmp_path / "preparation",
            companion_wheel=companion,
            cernora_wheel=cernora,
            companion_version="0.4.0",
            cernora_version="0.1.4",
        )


def test_preparation_accepts_explicit_successor_versions_and_ignores_local_filename(
    tmp_path: Path,
) -> None:
    companion = _wheel(
        tmp_path / "private-local-name.whl",
        distribution="cernora-reference-workflow",
        version="0.5.0",
    )
    cernora = _wheel(
        tmp_path / "another-local-name.whl",
        distribution="cernora",
        version="0.2.0",
    )
    destination = tmp_path / "preparation"

    manifest = create_study_preparation_bundle(
        destination,
        companion_wheel=companion,
        cernora_wheel=cernora,
        companion_version="0.5.0",
        cernora_version="0.2.0",
    )

    assert tuple(item.version for item in manifest.implementation_candidates) == (
        "0.2.0",
        "0.5.0",
    )
    serialized = (destination / "manifest.json").read_bytes()
    assert b"private-local-name" not in serialized
    assert b"another-local-name" not in serialized


def test_offline_script_creates_and_verifies_without_exposing_paths(tmp_path: Path) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "create_study_preparation_bundle.py"
    destination = tmp_path / "preparation"
    environment = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(root / "src"),
        "PYTHONNOUSERSITE": "1",
    }
    created = subprocess.run(
        (
            sys.executable,
            str(script),
            "create",
            "--companion-wheel",
            str(companion),
            "--companion-version",
            "0.4.0",
            "--cernora-wheel",
            str(cernora),
            "--cernora-version",
            "0.1.4",
            "--output",
            str(destination),
        ),
        env=environment,
        capture_output=True,
        check=False,
    )

    assert created.returncode == 0
    created_payload = json.loads(created.stdout)
    assert set(created_payload) == {"preparation_id", "status"}
    assert created_payload["status"] == "awaiting-user-decisions"
    assert created.stderr == b""
    assert str(tmp_path).encode() not in created.stdout

    verified = subprocess.run(
        (
            sys.executable,
            str(script),
            "verify",
            str(destination),
            "--companion-wheel",
            str(companion),
            "--cernora-wheel",
            str(cernora),
        ),
        env=environment,
        capture_output=True,
        check=False,
    )
    assert verified.returncode == 0
    assert verified.stdout == created.stdout
    assert verified.stderr == b""
