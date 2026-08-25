from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from cernora import (
    AuthorityBoundImportPackageV2,
    CaseProfile,
    EvidenceBundleV2,
    ProfileEvaluationContext,
    check_profile_conformance,
    evaluate_imported_case,
    import_evidence_bundle_v2,
    load_local_profile,
)

from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.profile import (
    OBSERVATION_ID,
    TEST_COMMAND,
    TEST_IDS,
    TEST_PLAN_SHA256,
    TEST_SOURCE_SHA256,
    ReferenceCodingProfile,
)

REPOSITORY = Path(__file__).resolve().parents[2]
PROFILE_DIRECTORY = REPOSITORY / "profiles/cernora-reference-coding-v1"
CONTEXT = ProfileEvaluationContext(
    evaluation_id="evaluation-1",
    evidence_id="evidence-1",
    score_id="score-1",
    source_receipt_sha256="9" * 64,
)


def _identity(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value.model_dump(mode="json", exclude_none=False)))


def _artifact(artifact_id: str, path: str, payload: bytes, media_type: str) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "media_type": media_type,
    }


def _result_payload(*, passed: bool, plan_sha256: str = TEST_PLAN_SHA256) -> dict[str, object]:
    expected = (-1, -5, 5, 4)
    categories = ("fail-to-pass", "fail-to-pass", "pass-to-pass", "pass-to-pass")
    tests = []
    for index, test_id in enumerate(TEST_IDS):
        case_passed = passed or index > 0
        tests.append(
            {
                "test_id": test_id,
                "category": categories[index],
                "passed": case_passed,
                "expected": expected[index],
                "actual": expected[index] if case_passed else 3,
                "error": None,
            }
        )
    return {
        "schema_version": "cernora.reference.test-results/v1",
        "test_plan_sha256": plan_sha256,
        "test_source_sha256": TEST_SOURCE_SHA256,
        "termination": "exited",
        "exit_code": 0 if passed else 1,
        "tests": tests,
        "runner_error": None,
        "pre_candidate_tree_sha256": "a" * 64,
        "post_candidate_tree_sha256": "b" * 64,
        "changed_paths": ["src/calc.py"],
        "protected_paths_unchanged": True,
    }


def _bound_package(
    results: bytes | None,
    *,
    action_tool: str = "run_frozen_test_plan",
) -> AuthorityBoundImportPackageV2:
    profile = ReferenceCodingProfile()
    authority = profile.authority
    case = authority.cases[0]
    artifacts: list[dict[str, object]] = []
    actions: list[dict[str, object]] = []
    artifact_bytes: dict[str, bytes] = {}
    if results is None:
        failure = {
            "domain": "evidence",
            "code": "missing_test_results",
            "message": "the frozen Test Runner receipt is unavailable",
        }
        terminal: dict[str, object] = {
            "status": "inconclusive",
            "answer": None,
            "failure": failure,
        }
    else:
        try:
            parsed = json.loads(results)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {}
        exit_code = parsed.get("exit_code", 0) if isinstance(parsed, dict) else 0
        result_artifact = _artifact(
            "test-results", "evidence/test-results.json", results, "application/json"
        )
        stderr = b""
        stderr_artifact = _artifact(
            "test-stderr",
            "evidence/test-stderr.txt",
            stderr,
            "text/plain; charset=utf-8",
        )
        terminal_bytes = b'{"state":"completed"}'
        terminal_artifact = _artifact(
            "terminal-record", "evidence/terminal.json", terminal_bytes, "application/json"
        )
        artifacts.extend((result_artifact, stderr_artifact, terminal_artifact))
        artifact_bytes.update(
            {
                "test-results": results,
                "test-stderr": stderr,
                "terminal-record": terminal_bytes,
            }
        )
        action: dict[str, object] = {
            "sequence": 0,
            "invocation_id": "test-runner-1",
            "tool": action_tool,
            "argv": TEST_COMMAND,
            "result": {
                "status": "completed" if exit_code == 0 else "failed",
                "exit_code": exit_code,
                "committed": True,
                "delivered": True,
                "stdout_artifact": {
                    "artifact_id": "test-results",
                    "sha256": result_artifact["sha256"],
                },
                "stderr_artifact": {
                    "artifact_id": "test-stderr",
                    "sha256": stderr_artifact["sha256"],
                },
            },
            "previous_receipt_sha256": None,
        }
        action["receipt_sha256"] = sha256_bytes(canonical_json_bytes(action))
        actions.append(action)
        terminal = {
            "status": "completed",
            "answer": {
                "content": terminal_bytes.decode(),
                "sha256": terminal_artifact["sha256"],
                "artifact": {
                    "artifact_id": "terminal-record",
                    "sha256": terminal_artifact["sha256"],
                },
            },
            "failure": None,
        }

    payload: dict[str, object] = {
        "schema_version": "agent.evaluator.evidence-bundle/v2",
        "bundle_id": "reference-test-bundle",
        "producer": {
            "producer_id": "cernora-reference-workflow",
            "producer_version": "1",
        },
        "run": {"run_id": "run-1", "attempt_id": "attempt-1"},
        "profile": {
            "profile_id": authority.profile_id,
            "profile_version": authority.profile_version,
            "sha256": _identity(authority),
        },
        "case": {
            "case_id": case.case_id,
            "case_version": case.case_version,
            "case_set": case.case_set,
            "sha256": _identity(case),
        },
        "fixtures": tuple(fixture.model_dump(mode="json") for fixture in case.fixture_references),
        "tool_actions": tuple(actions),
        "artifacts": tuple(artifacts),
        "terminal": terminal,
        "infrastructure": {"status": "valid", "failure": None},
    }
    payload["bundle_sha256"] = sha256_bytes(canonical_json_bytes(payload))
    bundle = EvidenceBundleV2.model_validate(payload)
    content = cast(Any, SimpleNamespace(bundle=bundle, artifact_bytes=artifact_bytes))
    return AuthorityBoundImportPackageV2(content=content, profile=authority, case=case)


def _assessment(results: bytes | None, *, action_tool: str = "run_frozen_test_plan") -> Any:
    profile = ReferenceCodingProfile()
    package = _bound_package(results, action_tool=action_tool)
    return profile.assess(package, CONTEXT)


def _evaluate_public_package(
    root: Path,
    results: bytes | None,
) -> str:
    source = root / "source"
    source.mkdir()
    bound = _bound_package(results)
    bundle = bound.content.bundle
    (source / "bundle.json").write_bytes(
        canonical_json_bytes(bundle.model_dump(mode="json", exclude_none=False))
    )
    for artifact in bundle.artifacts:
        destination = source / artifact.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bound.content.artifact_bytes[artifact.artifact_id])
    profile = ReferenceCodingProfile()
    import_evidence_bundle_v2(
        profile=profile,
        bundle_path=source / "bundle.json",
        output=root / "imported",
    )
    receipt = evaluate_imported_case(profile, root / "imported", root / "evaluated")
    return receipt.case_outcome


def test_profile_authority_is_explicit_conformant_and_loadable() -> None:
    profile = ReferenceCodingProfile()
    conformance = check_profile_conformance(profile)
    assert conformance.profile_id == "cernora-reference-coding-v1"
    assert conformance.profile_version == "1.0.0"
    assert conformance.case_ids == ("tiny-calculator-v1", "tiny-calculator-v2")
    stored = CaseProfile.model_validate_json((PROFILE_DIRECTORY / "profile.json").read_bytes())
    assert stored == profile.authority
    assert load_local_profile(PROFILE_DIRECTORY).authority == profile.authority


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        (canonical_json_bytes(_result_payload(passed=True)), "pass"),
        (canonical_json_bytes(_result_payload(passed=False)), "fail"),
        (None, "inconclusive"),
    ],
)
def test_public_sdk_evaluation_produces_three_outcomes(
    tmp_path: Path,
    results: bytes | None,
    expected: str,
) -> None:
    assert _evaluate_public_package(tmp_path, results) == expected


@pytest.mark.parametrize(
    ("passed", "applicability", "value"),
    [(True, "observed", True), (False, "observed", False)],
)
def test_assessment_uses_only_authoritative_test_results(
    passed: bool,
    applicability: str,
    value: bool,
) -> None:
    results = canonical_json_bytes(_result_payload(passed=passed))
    assessment = _assessment(results)
    observation = assessment.score.observations[0]
    assert observation.observation_id == OBSERVATION_ID
    assert observation.applicability == applicability
    assert observation.value is value
    assert assessment.result_records == ()
    assert assessment == _assessment(results)


@pytest.mark.parametrize(
    "results",
    [
        None,
        b"{",
        canonical_json_bytes(_result_payload(passed=True, plan_sha256="c" * 64)),
    ],
)
def test_missing_malformed_or_authority_mismatched_results_are_inconclusive(
    results: bytes | None,
) -> None:
    assessment = _assessment(results)
    observation = assessment.score.observations[0]
    assert observation.applicability == "invalid"
    assert observation.value is None
    assert observation.reason is not None
    assert assessment.evidence.failures[0].code == "authoritative_receipt_invalid"


def test_action_authority_mismatch_is_inconclusive() -> None:
    results = canonical_json_bytes(_result_payload(passed=True))
    observation = _assessment(results, action_tool="untrusted_runner").score.observations[0]
    assert observation.applicability == "invalid"
    assert observation.value is None


def test_import_package_authority_mismatch_is_rejected() -> None:
    profile = ReferenceCodingProfile()
    package = _bound_package(canonical_json_bytes(_result_payload(passed=True)))
    mismatched = package.profile.model_copy(update={"profile_version": "2.0.0"})
    package = AuthorityBoundImportPackageV2(
        content=package.content,
        profile=mismatched,
        case=package.case,
    )
    with pytest.raises(ValueError, match="not bound"):
        profile.assess(package, CONTEXT)


def test_profile_runtime_imports_only_the_cernora_package_root() -> None:
    sources = (
        REPOSITORY / "src/cernora_reference_workflow/profile.py",
        PROFILE_DIRECTORY / "profile.py",
    )
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "from cernora." not in text
        assert "import cernora." not in text
