"""Companion-owned Profile authority for the tiny-calculator reference workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from cernora import (
    Artifact,
    AuthorityBoundImportPackageV2,
    Case,
    CaseInput,
    CaseProfile,
    Evidence,
    EvidenceReference,
    Failure,
    FixtureReference,
    GatePolicy,
    Profile,
    ProfileAssessment,
    ProfileEvaluationContext,
    Score,
    ScoreObservation,
    ScorerPolicy,
    ToolAction,
    external_producer_identity,
)

PROFILE_ID = "cernora-reference-coding-v1"
PROFILE_VERSION = "1.0.0"
CASE_ID = "tiny-calculator-v1"
PROJECTION_VERSION = "cernora.reference.coding-projection/v1"
OBSERVATION_ID = "authoritative_tests_passed"
TEST_PLAN_SHA256 = "58f35bc22fc2834e036f06620881dea32e29d24e9125827f7b2e5c7a5fee1089"
TEST_SOURCE_SHA256 = "d02000a75cd10d97fb691368ac80cb9c1187e1af31bf2cc591423fe3f2407dd4"
PROFILE_TEST_PLAN_FIXTURE_SHA256 = TEST_PLAN_SHA256
TEST_COMMAND = ("python", "/tests/run_tests.py", "--candidate-root", "/workspace")
TEST_IDS = (
    "fail_to_pass_negative_left",
    "fail_to_pass_both_negative",
    "pass_to_pass_positive",
    "pass_to_pass_zero",
)
_CATEGORIES = ("fail-to-pass", "fail-to-pass", "pass-to-pass", "pass-to-pass")
_EXPECTED_VALUES = (-1, -5, 5, 4)
_DIGEST_LENGTH = 64


@dataclass(frozen=True)
class _CaseAuthority:
    case_id: str
    case_version: str
    prompt: str
    test_authority_id: str
    test_plan_sha256: str
    test_source_sha256: str
    fixture_id: str
    fixture_path: str
    test_ids: tuple[str, ...]
    categories: tuple[str, ...]
    expected_values: tuple[int, ...]


_CASE_AUTHORITIES = (
    _CaseAuthority(
        case_id=CASE_ID,
        case_version="1.0.0",
        prompt=(
            "Repair integer addition in src/calc.py without changing protected files, then "
            "evaluate the frozen task-owned four-case test receipt."
        ),
        test_authority_id="tiny-calculator-test-runner",
        test_plan_sha256=TEST_PLAN_SHA256,
        test_source_sha256=TEST_SOURCE_SHA256,
        fixture_id="tiny-calculator-test-plan",
        fixture_path="resources/test-plan.json",
        test_ids=TEST_IDS,
        categories=_CATEGORIES,
        expected_values=_EXPECTED_VALUES,
    ),
    _CaseAuthority(
        case_id="tiny-calculator-v2",
        case_version="2.0.0",
        prompt=(
            "Complete the strict signed-32-bit integer expression evaluator in src/calc.py "
            "without changing protected files, then evaluate the frozen twelve-case receipt."
        ),
        test_authority_id="tiny-calculator-v2-test-runner",
        test_plan_sha256="e788bee63f41a4b4a60ecc33319cb45eca2c5e2a4de85c334961ecaf3d27bcfc",
        test_source_sha256="6b792f9c72c8ec4b1c60883e64a958e477b9f7415a1bd24da4a36ac716082ac5",
        fixture_id="tiny-calculator-v2-test-plan",
        fixture_path="resources/tiny-calculator-v2-test-plan.json",
        test_ids=(
            "fail_to_pass_precedence",
            "fail_to_pass_parentheses",
            "fail_to_pass_unary_chain",
            "fail_to_pass_truncating_division",
            "fail_to_pass_whitespace",
            "fail_to_pass_add_overflow",
            "fail_to_pass_literal_overflow",
            "fail_to_pass_division_zero",
            "fail_to_pass_leading_zero",
            "pass_to_pass_literal",
            "pass_to_pass_simple_add",
            "pass_to_pass_negative_literal",
        ),
        categories=("fail-to-pass",) * 9 + ("pass-to-pass",) * 3,
        expected_values=(14, 20, 3, -2, 4, 1, 1, 1, 1, 7, 5, -4),
    ),
)
_CASE_AUTHORITY_BY_ID = {authority.case_id: authority for authority in _CASE_AUTHORITIES}


def _build_authority() -> CaseProfile:
    return CaseProfile(
        schema_version="agent.evaluator.case-profile/v1",
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
        description=(
            "Offline assessment of frozen tiny-calculator Test Runner receipts; Runtime prose "
            "and harness rewards are non-authoritative."
        ),
        cases=tuple(
            Case(
                case_id=authority.case_id,
                case_version=authority.case_version,
                case_set="cernora-reference-coding",
                input=CaseInput(
                    prompt=authority.prompt,
                    parameters={
                        "test_authority_id": authority.test_authority_id,
                        "test_authority_version": "1",
                        "test_plan_sha256": authority.test_plan_sha256,
                        "test_source_sha256": authority.test_source_sha256,
                        "test_command": list(TEST_COMMAND),
                        "test_ids": list(authority.test_ids),
                        "allowed_paths": ["src/calc.py"],
                        "protected_paths": ["pyproject.toml", "tests"],
                    },
                ),
                declared_capabilities=("offline-authoritative-test-receipt",),
                fixture_references=(
                    FixtureReference(
                        fixture_id=authority.fixture_id,
                        path=authority.fixture_path,
                        sha256=authority.test_plan_sha256,
                    ),
                ),
                tags=("coding", "repair", "deterministic"),
            )
            for authority in _CASE_AUTHORITIES
        ),
        scorer_policy=ScorerPolicy(
            policy_version="cernora.reference.coding-scorer/v1",
            required_observations=(OBSERVATION_ID,),
        ),
        gate_policy=GatePolicy(
            policy_version="cernora.reference.coding-gate/v1",
            required_score_ids=("reference-test-score",),
            invalid_result="inconclusive",
        ),
    )


@dataclass(frozen=True)
class _Analysis:
    verdict: Literal["pass", "fail", "inconclusive"]
    reason: str | None
    reference: EvidenceReference


def _strict_object(payload: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON member: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number is forbidden: {value}")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("test results are not strict UTF-8 JSON") from exc
    if type(value) is not dict:
        raise ValueError("test results must be a JSON object")
    return value


def _is_digest(value: object) -> bool:
    if type(value) is not str or len(value) != _DIGEST_LENGTH:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return value == value.lower()


def _validate_results(value: dict[str, Any], authority: _CaseAuthority) -> bool:
    expected_members = {
        "schema_version",
        "test_plan_sha256",
        "test_source_sha256",
        "termination",
        "exit_code",
        "tests",
        "runner_error",
        "pre_candidate_tree_sha256",
        "post_candidate_tree_sha256",
        "changed_paths",
        "protected_paths_unchanged",
    }
    if set(value) != expected_members:
        raise ValueError("test results have an invalid member set")
    if value["schema_version"] != "cernora.reference.test-results/v1":
        raise ValueError("test results schema version mismatch")
    if value["test_plan_sha256"] != authority.test_plan_sha256:
        raise ValueError("test plan authority digest mismatch")
    if value["test_source_sha256"] != authority.test_source_sha256:
        raise ValueError("test source authority digest mismatch")
    if not _is_digest(value["pre_candidate_tree_sha256"]) or not _is_digest(
        value["post_candidate_tree_sha256"]
    ):
        raise ValueError("candidate tree digest is invalid")
    if value["termination"] != "exited":
        raise ValueError("authoritative test process did not exit")
    if type(value["exit_code"]) is not int or value["runner_error"] is not None:
        raise ValueError("exited test results have invalid process fields")
    if type(value["protected_paths_unchanged"]) is not bool:
        raise ValueError("protected-path result is not boolean")
    changed_paths = value["changed_paths"]
    if type(changed_paths) is not list or any(type(path) is not str for path in changed_paths):
        raise ValueError("changed paths are invalid")
    if len(changed_paths) != len(set(changed_paths)):
        raise ValueError("changed paths are not unique")

    tests = value["tests"]
    if type(tests) is not list or len(tests) != len(authority.test_ids):
        raise ValueError("test results do not contain the complete authority case set")
    all_passed = True
    for index, item in enumerate(tests):
        if type(item) is not dict or set(item) != {
            "test_id",
            "category",
            "passed",
            "expected",
            "actual",
            "error",
        }:
            raise ValueError("test case result has an invalid member set")
        if (
            item["test_id"] != authority.test_ids[index]
            or item["category"] != authority.categories[index]
            or type(item["passed"]) is not bool
            or type(item["expected"]) is not int
            or item["expected"] != authority.expected_values[index]
            or (item["actual"] is not None and type(item["actual"]) is not int)
            or (item["error"] is not None and type(item["error"]) is not str)
        ):
            raise ValueError("test case result does not match the frozen authority")
        if item["passed"] and (item["actual"] != item["expected"] or item["error"] is not None):
            raise ValueError("passing test case result is internally inconsistent")
        all_passed = all_passed and item["passed"]

    return bool(
        value["exit_code"] == 0
        and all_passed
        and value["protected_paths_unchanged"]
        and set(changed_paths).issubset({"src/calc.py"})
    )


class ReferenceCodingProfile:
    """Assess only the frozen Test Runner artifact imported by Cernora."""

    def __init__(self) -> None:
        self._authority = _build_authority()

    @property
    def authority(self) -> CaseProfile:
        return self._authority

    @property
    def projection_version(self) -> str:
        return PROJECTION_VERSION

    def validate_import(self, package: AuthorityBoundImportPackageV2) -> None:
        if package.profile != self._authority:
            raise ValueError("import package is not bound to this Profile authority")
        if package.case not in self._authority.cases:
            raise ValueError("import package is not bound to the tiny-calculator Case")
        try:
            authority = _CASE_AUTHORITY_BY_ID[package.case.case_id]
        except KeyError as exc:
            raise ValueError("bundle Case is not a supported tiny-calculator authority") from exc
        bundle = package.content.bundle
        if (
            bundle.profile.profile_id,
            bundle.profile.profile_version,
        ) != (PROFILE_ID, PROFILE_VERSION):
            raise ValueError("bundle Profile identity mismatch")
        if (
            bundle.case.case_id,
            bundle.case.case_version,
            bundle.case.case_set,
        ) != (authority.case_id, authority.case_version, "cernora-reference-coding"):
            raise ValueError("bundle Case identity mismatch")
        expected_fixtures = tuple(
            fixture.model_dump(mode="json") for fixture in package.case.fixture_references
        )
        actual_fixtures = tuple(fixture.model_dump(mode="json") for fixture in bundle.fixtures)
        if actual_fixtures != expected_fixtures:
            raise ValueError("bundle Fixture identities do not match the Case authority")
        if (
            bundle.producer.producer_id,
            bundle.producer.producer_version,
        ) != ("cernora-reference-workflow", "1"):
            raise ValueError("bundle producer is not the companion adapter authority")

    def _analyze(
        self,
        package: AuthorityBoundImportPackageV2,
        context: ProfileEvaluationContext,
    ) -> _Analysis:
        receipt_reference = EvidenceReference(
            evidence_id=context.evidence_id,
            locator="source-import/import-receipt.json",
            sha256=context.source_receipt_sha256,
        )
        bundle = package.content.bundle
        authority = _CASE_AUTHORITY_BY_ID[package.case.case_id]
        try:
            if bundle.terminal.status != "completed":
                raise ValueError("authoritative test results are unavailable")
            if len(bundle.tool_actions) != 1:
                raise ValueError("expected exactly one frozen Test Runner action")
            action = bundle.tool_actions[0]
            if (
                action.sequence != 0
                or action.invocation_id != "test-runner-1"
                or action.tool != "run_frozen_test_plan"
                or action.argv != TEST_COMMAND
                or action.result.status not in {"completed", "failed"}
                or type(action.result.exit_code) is not int
                or not action.result.committed
                or not action.result.delivered
                or action.result.stdout_artifact.artifact_id != "test-results"
                or action.result.stderr_artifact.artifact_id != "test-stderr"
            ):
                raise ValueError("Test Runner action shape does not match authority")
            artifacts = {artifact.artifact_id: artifact for artifact in bundle.artifacts}
            if set(artifacts) != {"test-results", "test-stderr", "terminal-record"}:
                raise ValueError("completed bundle artifact set does not match authority")
            results_artifact = artifacts["test-results"]
            stderr_artifact = artifacts["test-stderr"]
            terminal_artifact = artifacts["terminal-record"]
            if (
                (results_artifact.path, results_artifact.media_type)
                != ("evidence/test-results.json", "application/json")
                or (stderr_artifact.path, stderr_artifact.media_type)
                != ("evidence/test-stderr.txt", "text/plain; charset=utf-8")
                or (terminal_artifact.path, terminal_artifact.media_type)
                != ("evidence/terminal.json", "application/json")
                or action.result.stdout_artifact.sha256 != results_artifact.sha256
                or action.result.stderr_artifact.sha256 != stderr_artifact.sha256
            ):
                raise ValueError("Test Runner artifact binding does not match authority")
            terminal = bundle.terminal.answer
            if (
                terminal is None
                or terminal.artifact.artifact_id != "terminal-record"
                or terminal.artifact.sha256 != terminal_artifact.sha256
            ):
                raise ValueError("terminal artifact binding does not match authority")
            results = _strict_object(package.content.artifact_bytes["test-results"])
            passed = _validate_results(results, authority)
            expected_status = "completed" if results["exit_code"] == 0 else "failed"
            if (
                action.result.exit_code != results["exit_code"]
                or action.result.status != expected_status
            ):
                raise ValueError("Test Runner action contradicts its structured results")
            reference = EvidenceReference(
                evidence_id=context.evidence_id,
                locator="artifacts/evidence/test-results.json",
                sha256=results_artifact.sha256,
            )
            return _Analysis("pass" if passed else "fail", None, reference)
        except (KeyError, TypeError, ValueError) as exc:
            reason = f"authoritative_receipt_invalid:{exc}"
            return _Analysis("inconclusive", reason, receipt_reference)

    def assess(
        self,
        package: AuthorityBoundImportPackageV2,
        context: ProfileEvaluationContext,
    ) -> ProfileAssessment:
        self.validate_import(package)
        analysis = self._analyze(package, context)
        applicability: Literal["observed", "invalid"]
        value: bool | None
        if analysis.verdict == "inconclusive":
            applicability = "invalid"
            value = None
        else:
            applicability = "observed"
            value = analysis.verdict == "pass"
        observation = ScoreObservation(
            observation_id=OBSERVATION_ID,
            applicability=applicability,
            value=value,
            reason=analysis.reason,
            evidence_references=(analysis.reference,),
        )
        score = Score(
            schema_version="agent.evaluator.score/v1",
            score_id=context.score_id,
            evidence_id=context.evidence_id,
            scorer_version=self._authority.scorer_policy.policy_version,
            observations=(observation,),
        )
        failures: tuple[Failure, ...] = ()
        if analysis.verdict == "inconclusive":
            assert analysis.reason is not None
            failures = (
                Failure(
                    domain="evidence",
                    code="authoritative_receipt_invalid",
                    message=analysis.reason,
                    evidence_references=(analysis.reference,),
                ),
            )
        return ProfileAssessment(
            evidence=self._evidence(package, context, failures),
            score=score,
            required_observations=self._authority.scorer_policy.required_observations,
        )

    @staticmethod
    def _evidence(
        package: AuthorityBoundImportPackageV2,
        context: ProfileEvaluationContext,
        failures: tuple[Failure, ...],
    ) -> Evidence:
        bundle = package.content.bundle
        authority = _CASE_AUTHORITY_BY_ID[package.case.case_id]
        return Evidence(
            schema_version="agent.evaluator.evidence/v1",
            evidence_id=context.evidence_id,
            evaluation_id=context.evaluation_id,
            profile_id=package.profile.profile_id,
            case_id=package.case.case_id,
            run_id=bundle.run.run_id,
            producer=external_producer_identity(
                bundle.producer.producer_id,
                bundle.producer.producer_version,
            ),
            process=None,
            tool_actions=tuple(
                ToolAction(
                    invocation_id=action.invocation_id,
                    tool=action.tool,
                    argv=action.argv,
                    exit_code=action.result.exit_code,
                    timed_out=action.result.status == "timed_out",
                    response_sha256=action.result.stdout_artifact.sha256,
                    committed=action.result.committed,
                    delivered=action.result.delivered,
                )
                for action in bundle.tool_actions
            ),
            artifacts=tuple(
                Artifact(
                    artifact_id=artifact.artifact_id,
                    path=artifact.path,
                    sha256=artifact.sha256,
                    media_type=artifact.media_type,
                )
                for artifact in bundle.artifacts
            ),
            answer=None,
            failures=failures,
            metadata={
                "projection_version": PROJECTION_VERSION,
                "test_authority_id": authority.test_authority_id,
                "test_authority_version": "1",
            },
        )


def create_profile() -> Profile:
    """Return the explicit companion Profile; no discovery or registry is used."""

    return ReferenceCodingProfile()


__all__ = ["ReferenceCodingProfile", "create_profile"]
