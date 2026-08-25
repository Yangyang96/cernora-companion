"""Strict models for the task-owned deterministic Test Runner receipt."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    load_json_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.experiment_spec import Digest, NonEmpty, StrictContract

V1_TEST_IDS = (
    "fail_to_pass_negative_left",
    "fail_to_pass_both_negative",
    "pass_to_pass_positive",
    "pass_to_pass_zero",
)
TEST_IDS = V1_TEST_IDS


class TestPlan(StrictContract):
    schema_version: Literal["cernora.reference.test-plan/v1"]
    authority_id: Literal["tiny-calculator-test-runner", "tiny-calculator-v2-test-runner"]
    authority_version: Literal["1"]
    command: tuple[NonEmpty, ...]
    working_directory: Literal["/workspace"]
    test_ids: tuple[NonEmpty, ...]
    test_source_sha256: Digest
    allowed_paths: tuple[NonEmpty, ...]
    protected_paths: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def validate_cases(self) -> TestPlan:
        if not self.test_ids or len(self.test_ids) != len(set(self.test_ids)):
            raise ValueError("test plan must contain unique ordered authority cases")
        if self.allowed_paths != ("src/calc.py",):
            raise ValueError("test plan allowed-path authority mismatch")
        if self.protected_paths != ("pyproject.toml", "tests"):
            raise ValueError("test plan protected-path authority mismatch")
        return self

    @property
    def authority_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.model_dump(mode="json")))


class TestCaseResult(StrictContract):
    test_id: NonEmpty
    category: Literal["fail-to-pass", "pass-to-pass"]
    passed: StrictBool
    expected: StrictInt
    actual: StrictInt | None
    error: StrictStr | None


class RawTestResults(StrictContract):
    """Exact dependency-free Test Runner stdout contract."""

    schema_version: Literal["cernora.reference.test-results/v1"]
    termination: Literal["exited", "runner-error"]
    tests: tuple[TestCaseResult, ...]
    runner_error: StrictStr | None

    @model_validator(mode="after")
    def validate_receipt(self) -> RawTestResults:
        if self.termination == "exited":
            if self.runner_error is not None:
                raise ValueError("exited raw result cannot contain runner_error")
            test_ids = tuple(item.test_id for item in self.tests)
            if not test_ids or len(test_ids) != len(set(test_ids)):
                raise ValueError("raw result must contain unique ordered test results")
        elif self.tests or self.runner_error is None:
            raise ValueError("runner-error result requires only runner_error")
        return self


def canonical_raw_test_output(result: RawTestResults) -> bytes:
    return canonical_json_bytes(result.model_dump(mode="json")) + b"\n"


def parse_raw_test_output(data: bytes) -> RawTestResults:
    payload = load_json_bytes(data)
    if not isinstance(payload, dict):
        raise ContractError("Test Runner stdout must contain one JSON object")
    try:
        result = RawTestResults.model_validate(payload)
    except ValueError as exc:
        raise ContractError(f"invalid Test Runner stdout: {exc}") from exc
    if data != canonical_raw_test_output(result):
        raise ContractError("Test Runner stdout is not canonical JSON followed by one newline")
    return result


class TestResults(StrictContract):
    schema_version: Literal["cernora.reference.test-results/v1"]
    test_plan_sha256: Digest
    test_source_sha256: Digest
    termination: Literal["exited", "timed-out", "runner-error", "interrupted"]
    exit_code: StrictInt | None
    tests: tuple[TestCaseResult, ...]
    runner_error: StrictStr | None
    pre_candidate_tree_sha256: Digest
    post_candidate_tree_sha256: Digest
    changed_paths: tuple[NonEmpty, ...]
    protected_paths_unchanged: StrictBool

    @model_validator(mode="after")
    def validate_receipt(self) -> TestResults:
        if self.changed_paths != tuple(sorted(set(self.changed_paths))):
            raise ValueError("changed_paths must be uniquely sorted")
        if self.termination == "exited":
            if self.exit_code is None or self.runner_error is not None:
                raise ValueError("exited receipt requires exit_code and no runner_error")
            test_ids = tuple(item.test_id for item in self.tests)
            if not test_ids or len(test_ids) != len(set(test_ids)):
                raise ValueError("exited receipt must contain unique ordered test results")
        elif self.termination == "runner-error":
            if self.tests or self.exit_code is None or self.runner_error is None:
                raise ValueError("runner-error receipt requires exit_code and runner_error")
        elif self.tests or self.exit_code is not None or self.runner_error is None:
            raise ValueError(
                "timed-out or interrupted receipt requires no tests/exit_code and a reason"
            )
        return self

    @property
    def verdict(self) -> Literal["pass", "fail", "inconclusive"]:
        if self.termination != "exited" or self.exit_code is None:
            return "inconclusive"
        if (
            self.exit_code == 0
            and all(item.passed for item in self.tests)
            and self.protected_paths_unchanged
        ):
            return "pass"
        if self.tests:
            return "fail"
        return "inconclusive"


class ProcessReceipt(StrictContract):
    schema_version: Literal["cernora.reference.process-receipt/v1"]
    argv: tuple[NonEmpty, ...]
    working_directory: Literal["/workspace"]
    exit_code: StrictInt | None
    termination: Literal["exited", "timed-out", "runner-error", "interrupted"]
    stdout_sha256: Digest
    stderr_sha256: Digest

    @model_validator(mode="after")
    def validate_termination(self) -> ProcessReceipt:
        has_exit_code = self.exit_code is not None
        if (self.termination in {"exited", "runner-error"}) is not has_exit_code:
            raise ValueError("exited and runner-error processes require an exit code")
        return self


class ResourceReceipt(StrictContract):
    schema_version: Literal["cernora.reference.resource-receipt/v1"]
    duration_milliseconds: Annotated[StrictInt, Field(ge=0)] | None
    peak_memory_bytes: Annotated[StrictInt, Field(ge=0)] | None
    cpu_milliseconds: Annotated[StrictInt, Field(ge=0)] | None
