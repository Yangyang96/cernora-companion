"""Versioned, evidence-oriented records for controlled Python repair Cases."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from cernora import EvidenceReference, ResultRecord
from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from cernora_reference_workflow.common import canonical_content_id, validate_relative_path
from cernora_reference_workflow.controlled_experiment_spec import (
    Digest,
    Identifier,
    StrictV2Contract,
)

RESULT_RECORD_VERSION: Literal["agent.evaluator.result-record/v1"] = (
    "agent.evaluator.result-record/v1"
)
REPAIR_RESULT_SCHEMA_VERSION = "cernora.reference.repair-result/v1"
_BYTECODE_CACHE_DIRECTORY = "__pycache__"


def _is_bytecode_cache_artifact(path: str) -> bool:
    """One deterministic in-container import side effect, never agent authority.

    Importing a repaired module inside the Task container compiles its frozen
    source into ``__pycache__/<name>.cpython-*.pyc``. That byte-cache file is
    not authored work: it stays fresh by source mtime, so a stale cache cannot
    alter verification behavior, and a changed ``.pyc`` always accompanies the
    deliberately changed ``.py`` it derives from.
    """

    pure = PurePosixPath(path)
    return pure.suffix == ".pyc" and _BYTECODE_CACHE_DIRECTORY in pure.parts


class RepairCheck(StrictV2Contract):
    """One frozen test observation with a stable failure-migration code."""

    check_id: Identifier
    failure_code: Identifier
    passed: Annotated[StrictBool, Field(strict=True)]


class ProtectedPathReceipt(StrictV2Contract):
    """Tree identities proving whether protected paths changed."""

    before_sha256: Digest
    after_sha256: Digest
    unchanged: Annotated[StrictBool, Field(strict=True)]

    @model_validator(mode="after")
    def exact_unchanged_fact(self) -> Self:
        if self.unchanged != (self.before_sha256 == self.after_sha256):
            raise ValueError("protected-path unchanged fact contradicts tree digests")
        return self


class RepairResultRecord(StrictV2Contract):
    """One deterministic Test Runner receipt, independent of Runtime prose."""

    schema_version: Literal["cernora.reference.repair-result/v1"]
    result_id: Digest
    case_id: Identifier
    result_record_version: Literal["agent.evaluator.result-record/v1"]
    test_authority_sha256: Digest
    test_plan_sha256: Digest
    test_source_sha256: Digest
    termination: Literal["exited", "timed_out", "runner_error"]
    exit_code: StrictInt | None
    checks: Annotated[tuple[RepairCheck, ...], Field(min_length=1)]
    allowed_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    changed_paths: tuple[str, ...]
    protected_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    protected_path_receipt: ProtectedPathReceipt

    @field_validator("checks", "allowed_paths", "changed_paths", "protected_paths", mode="before")
    @classmethod
    def tuple_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def canonical_and_bound(self) -> Self:
        check_ids = tuple(item.check_id for item in self.checks)
        codes = tuple(item.failure_code for item in self.checks)
        if check_ids != tuple(sorted(check_ids)) or len(check_ids) != len(set(check_ids)):
            raise ValueError("repair checks must be sorted and unique by check_id")
        if len(codes) != len(set(codes)):
            raise ValueError("repair failure codes must be unique within one Case")
        for field_name, paths in (
            ("allowed", self.allowed_paths),
            ("changed", self.changed_paths),
            ("protected", self.protected_paths),
        ):
            normalized = tuple(validate_relative_path(item) for item in paths)
            if normalized != tuple(sorted(normalized)) or len(normalized) != len(set(normalized)):
                raise ValueError(f"{field_name} paths must be sorted and unique")
        if set(self.allowed_paths).intersection(self.protected_paths):
            raise ValueError("allowed and protected paths must be disjoint")
        if self.termination == "exited" and self.exit_code is None:
            raise ValueError("exited repair results require an exit code")
        if self.termination != "exited" and self.exit_code is not None:
            raise ValueError("non-exited repair results cannot carry an exit code")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"result_id"})
        )
        if self.result_id != expected:
            raise ValueError("repair result identity does not match canonical content")
        return self

    @property
    def evaluation_valid(self) -> bool:
        return self.termination == "exited"

    @property
    def passed(self) -> bool:
        return bool(
            self.evaluation_valid
            and self.exit_code == 0
            and all(item.passed for item in self.checks)
            and self.protected_path_receipt.unchanged
            and self.authority_changed_paths.issubset(self.allowed_paths)
        )

    @property
    def authority_changed_paths(self) -> frozenset[str]:
        """Changed paths that count for path authority, minus bytecode caches."""

        return frozenset(
            path for path in self.changed_paths if not _is_bytecode_cache_artifact(path)
        )

    @property
    def failure_codes(self) -> tuple[str, ...]:
        if not self.evaluation_valid:
            return ("evaluation_unavailable_v1",)
        result = [item.failure_code for item in self.checks if not item.passed]
        if not self.protected_path_receipt.unchanged:
            result.append("protected_path_changed_v1")
        if not self.authority_changed_paths.issubset(self.allowed_paths):
            result.append("unauthorized_path_changed_v1")
        if self.exit_code not in {None, 0} and not result:
            result.append("test_process_failed_v1")
        return tuple(sorted(result))

    def core_result_records(self, reference: EvidenceReference) -> tuple[ResultRecord, ...]:
        """Project the receipt into Core-native, versioned decision records."""

        if not self.evaluation_valid:
            unavailable_records: tuple[tuple[str, Literal["constraint", "outcome"]], ...] = (
                ("authorized_paths_only_v1", "constraint"),
                ("protected_paths_unchanged_v1", "constraint"),
                ("repair_success_v1", "outcome"),
            )
            return tuple(
                ResultRecord(
                    id=record_id,
                    version=RESULT_RECORD_VERSION,
                    role=role,
                    value=None,
                    value_type="boolean",
                    validity="unavailable",
                    failure_reason="authoritative repair test result unavailable",
                    evidence_refs=(reference,),
                    unit=None,
                    direction=None,
                )
                for record_id, role in unavailable_records
            )
        records: list[ResultRecord] = [
            ResultRecord(
                id="repair_success_v1",
                version=RESULT_RECORD_VERSION,
                role="outcome",
                value=self.passed,
                value_type="boolean",
                validity="valid",
                failure_reason=None,
                evidence_refs=(reference,),
                unit=None,
                direction=None,
            )
        ]
        records.extend(
            (
                ResultRecord(
                    id="protected_paths_unchanged_v1",
                    version=RESULT_RECORD_VERSION,
                    role="constraint",
                    value=self.protected_path_receipt.unchanged,
                    value_type="boolean",
                    validity="valid",
                    failure_reason=None,
                    evidence_refs=(reference,),
                    unit=None,
                    direction=None,
                ),
                ResultRecord(
                    id="authorized_paths_only_v1",
                    version=RESULT_RECORD_VERSION,
                    role="constraint",
                    value=self.authority_changed_paths.issubset(self.allowed_paths),
                    value_type="boolean",
                    validity="valid",
                    failure_reason=None,
                    evidence_refs=(reference,),
                    unit=None,
                    direction=None,
                ),
            )
        )
        records.extend(
            ResultRecord(
                id=f"diagnostic.{item.failure_code}",
                version=RESULT_RECORD_VERSION,
                role="diagnostic",
                value=item.passed,
                value_type="boolean",
                validity="valid",
                failure_reason=None,
                evidence_refs=(reference,),
                unit=None,
                direction=None,
            )
            for item in self.checks
        )
        return tuple(sorted(records, key=lambda item: item.id))


def materialize_repair_result(payload_without_identity: dict[str, object]) -> RepairResultRecord:
    """Add the canonical identity to one strict Test Runner receipt."""

    if "result_id" in payload_without_identity:
        raise ValueError("repair result materialization input must omit result_id")
    payload = dict(payload_without_identity)
    payload["result_id"] = canonical_content_id(payload, excluded=frozenset())
    return RepairResultRecord.model_validate(payload)


__all__ = [
    "REPAIR_RESULT_SCHEMA_VERSION",
    "RESULT_RECORD_VERSION",
    "ProtectedPathReceipt",
    "RepairCheck",
    "RepairResultRecord",
    "materialize_repair_result",
]
