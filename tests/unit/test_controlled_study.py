from __future__ import annotations

import fcntl
import os
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

import cernora_reference_workflow.controlled_study as controlled_study_module
from cernora_reference_workflow.candidate_development import freeze_candidate_development
from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.controlled_study import (
    ControlledStudyError,
    advance,
    compile_study_protocol,
    materialize_execution_outcome,
    materialize_heldout_commitment,
    materialize_heldout_reveal,
    materialize_implementation_lock,
    materialize_study_analysis_policy,
    materialize_study_artifact_manifest,
    materialize_study_intent,
    prepare,
)
from tests.unit.test_candidate_development import candidate_development_payload


def implementation_payload(*, analysis_sha256: str = "5" * 64) -> dict[str, object]:
    return {
        "schema_version": "cernora.reference.implementation-lock/v1",
        "companion": {
            "name": "cernora-reference-workflow",
            "version": "0.4.0",
            "kind": "wheel",
            "sha256": "1" * 64,
        },
        "cernora": {
            "name": "cernora",
            "version": "0.1.4",
            "kind": "wheel",
            "sha256": "2" * 64,
        },
        "runtime_adapter": {
            "name": "harbor-codex-adapter",
            "version": "1",
            "kind": "source-tree",
            "sha256": "3" * 64,
        },
        "harness": {
            "name": "harbor",
            "version": "0.16.1",
            "kind": "wheel",
            "sha256": "4" * 64,
        },
        "analysis_policy": {
            "name": "controlled-study-policy",
            "version": "1",
            "kind": "policy-bundle",
            "sha256": analysis_sha256,
        },
    }


def study_intent_payload() -> dict[str, object]:
    candidate_development = freeze_candidate_development(candidate_development_payload())
    analysis_policy = materialize_study_analysis_policy(
        {
            "schema_version": "cernora.reference.study-analysis-policy/v1",
            "primary_outcome": "paired-reliable-success-rate-delta",
            "bootstrap_resamples": 10000,
            "confidence_level": "0.95",
            "guardrail_rule": "no-protected-regression",
            "missing_evidence": "inconclusive",
            "claim_source": "held-out-only",
        }
    )
    analysis_bytes = canonical_json_bytes(analysis_policy.model_dump(mode="json"))
    heldout_case = {
        "case_id": "held-interval",
        "split": "held-out",
        "authority_sha256": "e" * 64,
    }
    heldout_root = sha256_bytes(canonical_json_bytes([heldout_case]))
    return {
        "schema_version": "cernora.reference.study-intent/v1",
        "study_kind": "contract-proof",
        "candidate_development": candidate_development.model_dump(mode="json"),
        "cases": [
            {
                "case_id": "dev-ledger",
                "split": "development",
                "authority_sha256": "d" * 64,
            },
            heldout_case,
            {
                "case_id": "reg-query",
                "split": "regression",
                "authority_sha256": "f" * 64,
            },
        ],
        "repetitions": 2,
        "max_attempt_count": 12,
        "max_wall_seconds": 7200,
        "heldout_commitment": materialize_heldout_commitment(
            {
                "schema_version": "cernora.reference.heldout-commitment/v1",
                "manifest_sha256": "7" * 64,
                "case_count": 1,
                "case_commitment_root_sha256": heldout_root,
                "reveal_policy_sha256": "9" * 64,
            }
        ).model_dump(mode="json"),
        "analysis_policy": analysis_policy.model_dump(mode="json"),
        "implementation_lock": materialize_implementation_lock(
            implementation_payload(analysis_sha256=sha256_bytes(analysis_bytes))
        ).model_dump(mode="json"),
    }


def test_implementation_lock_binds_exact_artifact_bytes() -> None:
    first = materialize_implementation_lock(implementation_payload())
    changed = deepcopy(implementation_payload())
    assert isinstance(changed["runtime_adapter"], dict)
    changed["runtime_adapter"]["sha256"] = "6" * 64
    second = materialize_implementation_lock(changed)

    assert first.lock_id != second.lock_id
    assert first.companion.sha256 == "1" * 64
    assert first.runtime_adapter.sha256 == "3" * 64


def test_implementation_lock_rejects_version_without_digest() -> None:
    payload = implementation_payload()
    assert isinstance(payload["companion"], dict)
    del payload["companion"]["sha256"]

    with pytest.raises(ValidationError):
        materialize_implementation_lock(payload)


def test_protocol_separates_claim_scopes_and_counterbalances_pairs() -> None:
    protocol = compile_study_protocol(materialize_study_intent(study_intent_payload()))

    assert protocol.planned_trial_count == 12
    assert tuple((item.case_id, item.repetition) for item in protocol.blocks) == (
        ("dev-ledger", 1),
        ("held-interval", 1),
        ("reg-query", 1),
        ("dev-ledger", 2),
        ("held-interval", 2),
        ("reg-query", 2),
    )
    assert tuple(item.configuration_order for item in protocol.blocks) == (
        ("baseline", "candidate"),
        ("candidate", "baseline"),
        ("baseline", "candidate"),
        ("candidate", "baseline"),
        ("baseline", "candidate"),
        ("candidate", "baseline"),
    )
    assert protocol.claims.development == "descriptive"
    assert protocol.claims.regression == "guardrail"
    assert protocol.claims.heldout == "confirmatory-primary"
    assert protocol.claims.descriptive_case_ids == ("dev-ledger",)
    assert protocol.claims.guardrail_case_ids == ("reg-query",)
    assert protocol.claims.primary_case_ids == ("held-interval",)
    assert protocol.claims.effect_conclusion == "descriptive-only"
    assert protocol.confirmatory_quality_stop is False


def test_terminal_outcomes_require_the_correct_closed_artifact() -> None:
    protocol = compile_study_protocol(materialize_study_intent(study_intent_payload()))
    common = {
        "schema_version": "cernora.reference.study-artifact-manifest/v1",
        "protocol_id": protocol.protocol_id,
        "implementation_lock_id": protocol.implementation_lock_id,
        "ledger_root_sha256": "9" * 64,
        "report_sha256": "0" * 64,
        "files": [
            {
                "path": "report.json",
                "byte_length": 1,
                "sha256": "0" * 64,
            }
        ],
    }
    diagnostic = materialize_study_artifact_manifest(
        {
            **common,
            "kind": "diagnostic-pack",
            "terminal_status": "paused",
            "claim_authority": "diagnostic-only",
            "batch_package_sha256": None,
            "comparison_package_sha256": None,
        }
    )
    paused = materialize_execution_outcome(
        {
            "schema_version": "cernora.reference.execution-outcome/v1",
            "study_id": "8" * 64,
            "protocol_id": protocol.protocol_id,
            "ledger_root_sha256": diagnostic.ledger_root_sha256,
            "status": "paused",
            "execution_id": "1" * 64,
            "reason": "operator-request",
            "artifact": {
                "kind": diagnostic.kind,
                "artifact_id": diagnostic.artifact_id,
            },
            "resumable": True,
        }
    )

    assert paused.status == "paused"
    assert paused.artifact.kind == "diagnostic-pack"

    complete = deepcopy(common)
    complete.update(
        {
            "kind": "evidence-pack",
            "terminal_status": "completed",
            "claim_authority": "descriptive-only",
            "batch_package_sha256": "a" * 64,
            "comparison_package_sha256": "b" * 64,
        }
    )
    evidence = materialize_study_artifact_manifest(complete)
    completed = materialize_execution_outcome(
        {
            "schema_version": "cernora.reference.execution-outcome/v1",
            "study_id": "8" * 64,
            "protocol_id": protocol.protocol_id,
            "ledger_root_sha256": evidence.ledger_root_sha256,
            "status": "completed",
            "execution_id": "2" * 64,
            "artifact": {"kind": evidence.kind, "artifact_id": evidence.artifact_id},
            "claim_authority": evidence.claim_authority,
        }
    )

    assert completed.status == "completed"
    assert completed.artifact.kind == "evidence-pack"

    invalid = deepcopy(complete)
    invalid["kind"] = "diagnostic-pack"
    with pytest.raises(ValidationError):
        materialize_study_artifact_manifest(invalid)


def test_structural_error_exposes_stable_code_and_phase() -> None:
    error = ControlledStudyError(
        "authority-mismatch",
        phase="prepare",
        artifact_id="f" * 64,
    )

    assert error.code == "authority-mismatch"
    assert error.phase == "prepare"
    assert error.artifact_id == "f" * 64
    assert str(error) == "prepare:authority-mismatch"


def test_prepare_publishes_one_strict_durable_ledger(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    custody_parent = repository / ".agent" / "test-controlled-study"
    custody_parent.mkdir(parents=True, exist_ok=True)
    destination = custody_parent / tmp_path.name
    intent = materialize_study_intent(study_intent_payload())

    try:
        first = prepare(intent, destination)
        repeated = prepare(intent, destination)

        assert repeated == first
        assert first.status == "prepared"
        assert first.study_id != first.protocol_id
        assert {path.relative_to(destination).as_posix() for path in destination.rglob("*")} == {
            ".writer.lock",
            "intent.json",
            "ledger",
            "ledger/00000001.json",
            "protocol.json",
            "study.json",
        }

        ledger = destination / "ledger" / "00000001.json"
        original = ledger.read_bytes()
        ledger.write_bytes(original.replace(b'"prepared"', b'"runningz"'))
        with pytest.raises(ControlledStudyError) as raised:
            prepare(intent, destination)
        assert raised.value.code == "corrupt-ledger"
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_prepare_rejects_temporary_custody(tmp_path: Path) -> None:
    with pytest.raises(ControlledStudyError) as raised:
        prepare(materialize_study_intent(study_intent_payload()), tmp_path / "study")

    assert raised.value.code == "invalid-intent"


def test_study_intent_rejects_development_evidence_outside_its_split() -> None:
    payload = study_intent_payload()
    assert isinstance(payload["candidate_development"], dict)
    observations = payload["candidate_development"]["observations"]
    assert isinstance(observations, list)
    assert isinstance(observations[0], dict)
    observations[0]["case_id"] = "held-interval"
    del payload["candidate_development"]["development_id"]
    payload["candidate_development"] = freeze_candidate_development(
        payload["candidate_development"]
    ).model_dump(mode="json")

    with pytest.raises(ValidationError):
        materialize_study_intent(payload)


def test_advance_is_idempotent_and_binds_fresh_acceptance(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    custody_parent = repository / ".agent" / "test-controlled-study"
    custody_parent.mkdir(parents=True, exist_ok=True)
    destination = custody_parent / f"advance-{tmp_path.name}"

    try:
        intent = materialize_study_intent(study_intent_payload())
        prepared = prepare(intent, destination)
        reveal = {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "request-reveal",
        }
        awaiting_reveal = advance(destination, reveal)
        assert advance(destination, reveal) == awaiting_reveal
        assert awaiting_reveal.status == "awaiting-reveal"

        awaiting_acceptance = advance(
            destination,
            {
                "schema_version": "cernora.reference.advance-directive/v1",
                "action": "bind-reveal",
                "reveal": materialize_heldout_reveal(
                    {
                        "schema_version": "cernora.reference.heldout-reveal/v1",
                        "commitment_id": intent.heldout_commitment.commitment_id,
                        "manifest_sha256": intent.heldout_commitment.manifest_sha256,
                        "cases": [
                            item.model_dump(mode="json")
                            for item in intent.cases
                            if item.split == "held-out"
                        ],
                    }
                ).model_dump(mode="json"),
            },
        )
        assert awaiting_acceptance.status == "awaiting-acceptance"

        with pytest.raises(ControlledStudyError) as stale:
            advance(
                destination,
                {
                    "schema_version": "cernora.reference.advance-directive/v1",
                    "action": "accept",
                    "acceptance_id": "0" * 64,
                },
            )
        assert stale.value.code == "stale-acceptance"

        accepted = {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "accept",
            "acceptance_id": awaiting_acceptance.acceptance_id,
        }
        running = advance(destination, accepted)
        assert advance(destination, accepted) == running
        assert running.status == "running"
        assert running.study_id == prepared.study_id
        assert len(tuple((destination / "ledger").glob("*.json"))) == 4
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_advance_rejects_a_reveal_outside_the_frozen_commitment(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    custody_parent = repository / ".agent" / "test-controlled-study"
    custody_parent.mkdir(parents=True, exist_ok=True)
    destination = custody_parent / f"reveal-{tmp_path.name}"
    intent = materialize_study_intent(study_intent_payload())

    try:
        prepare(intent, destination)
        advance(
            destination,
            {
                "schema_version": "cernora.reference.advance-directive/v1",
                "action": "request-reveal",
            },
        )
        reveal = materialize_heldout_reveal(
            {
                "schema_version": "cernora.reference.heldout-reveal/v1",
                "commitment_id": intent.heldout_commitment.commitment_id,
                "manifest_sha256": intent.heldout_commitment.manifest_sha256,
                "cases": [
                    {
                        "case_id": "held-interval",
                        "split": "held-out",
                        "authority_sha256": "0" * 64,
                    }
                ],
            }
        )
        with pytest.raises(ControlledStudyError) as mismatch:
            advance(
                destination,
                {
                    "schema_version": "cernora.reference.advance-directive/v1",
                    "action": "bind-reveal",
                    "reveal": reveal.model_dump(mode="json"),
                },
            )
        assert mismatch.value.code == "authority-mismatch"
        assert len(tuple((destination / "ledger").glob("*.json"))) == 2
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_advance_rejects_a_concurrent_writer(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    custody_parent = repository / ".agent" / "test-controlled-study"
    custody_parent.mkdir(parents=True, exist_ok=True)
    destination = custody_parent / f"writer-{tmp_path.name}"

    try:
        prepare(materialize_study_intent(study_intent_payload()), destination)
        descriptor = os.open(destination / ".writer.lock", os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(ControlledStudyError) as busy:
                advance(
                    destination,
                    {
                        "schema_version": "cernora.reference.advance-directive/v1",
                        "action": "request-reveal",
                    },
                )
            assert busy.value.code == "concurrent-writer"
            assert len(tuple((destination / "ledger").glob("*.json"))) == 1
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_advance_adopts_an_entry_after_post_link_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Path(__file__).resolve().parents[2]
    custody_parent = repository / ".agent" / "test-controlled-study"
    custody_parent.mkdir(parents=True, exist_ok=True)
    destination = custody_parent / f"adopt-{tmp_path.name}"
    directive = {
        "schema_version": "cernora.reference.advance-directive/v1",
        "action": "request-reveal",
    }

    try:
        prepare(materialize_study_intent(study_intent_payload()), destination)
        original_sync = controlled_study_module._sync_directory

        def crash_after_link(path: Path) -> None:
            if path == destination / "ledger":
                raise OSError("synthetic post-link crash")
            original_sync(path)

        monkeypatch.setattr(controlled_study_module, "_sync_directory", crash_after_link)
        with pytest.raises(OSError, match="post-link crash"):
            advance(destination, directive)
        monkeypatch.setattr(controlled_study_module, "_sync_directory", original_sync)

        adopted = advance(destination, directive)
        assert adopted.status == "awaiting-reveal"
        assert len(tuple((destination / "ledger").glob("*.json"))) == 2
    finally:
        shutil.rmtree(destination, ignore_errors=True)
