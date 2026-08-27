"""Generic, content-identified task authority for controlled repair Cases."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from cernora import Case, CaseInput, FixtureReference
from pydantic import Field, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
    validate_relative_path,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    Digest,
    Identifier,
    StrictV2Contract,
)
from cernora_reference_workflow.heldout_seal import HeldoutArchiveCase

MAX_TASK_FILE_BYTES = 2 * 1024 * 1024


class ControlledTaskFile(StrictV2Contract):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0, le=MAX_TASK_FILE_BYTES)
    sha256: Digest
    content_base64: str

    @model_validator(mode="after")
    def exact_content(self) -> Self:
        validate_relative_path(self.path)
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("task file is not canonical base64") from exc
        if base64.b64encode(content).decode("ascii") != self.content_base64:
            raise ValueError("task file is not canonical base64")
        if len(content) != self.size_bytes or sha256_bytes(content) != self.sha256:
            raise ValueError("task file content does not match its digest")
        return self

    def content(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


def _task_file(path: str, content: bytes) -> ControlledTaskFile:
    return ControlledTaskFile(
        path=path,
        size_bytes=len(content),
        sha256=sha256_bytes(content),
        content_base64=base64.b64encode(content).decode("ascii"),
    )


class ControlledTaskAuthority(StrictV2Contract):
    schema_version: Literal["cernora.reference.controlled-task-authority/v1"]
    authority_id: Digest
    case: Case
    split_id: Literal["development", "regression", "held-out"]
    failure_code: Identifier
    workspace_files: Annotated[tuple[ControlledTaskFile, ...], Field(min_length=1)]
    test_files: Annotated[tuple[ControlledTaskFile, ...], Field(min_length=1)]
    allowed_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    protected_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    test_command: Annotated[tuple[str, ...], Field(min_length=1)]

    @field_validator("case", mode="before")
    @classmethod
    def strict_case_collections(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        for field in ("declared_capabilities", "fixture_references", "tags"):
            item = payload.get(field)
            if isinstance(item, list):
                payload[field] = tuple(item)
        return payload

    @field_validator(
        "workspace_files",
        "test_files",
        "allowed_paths",
        "protected_paths",
        "test_command",
        mode="before",
    )
    @classmethod
    def tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def canonical_authority(self) -> Self:
        for label, files in (("workspace", self.workspace_files), ("test", self.test_files)):
            paths = tuple(item.path for item in files)
            if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
                raise ValueError(f"{label} task files must be sorted and unique")
        for label, paths in (("allowed", self.allowed_paths), ("protected", self.protected_paths)):
            normalized = tuple(validate_relative_path(item) for item in paths)
            if normalized != tuple(sorted(normalized)) or len(normalized) != len(set(normalized)):
                raise ValueError(f"{label} task paths must be sorted and unique")
        if set(self.allowed_paths).intersection(self.protected_paths):
            raise ValueError("allowed and protected task paths must be disjoint")
        if not set(self.allowed_paths).issubset({item.path for item in self.workspace_files}):
            raise ValueError("allowed paths are absent from the initial workspace")
        parameters = self.case.input.parameters
        expected = {
            "allowed_paths": list(self.allowed_paths),
            "failure_code": self.failure_code,
            "protected_paths": list(self.protected_paths),
            "test_command": list(self.test_command),
            "test_source_sha256": self.test_source_sha256,
        }
        if parameters != expected:
            raise ValueError("Case parameters do not bind the task authority")
        digest = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"authority_id"})
        )
        if self.authority_id != digest:
            raise ValueError("controlled task authority identity mismatch")
        return self

    @property
    def case_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.case.model_dump(mode="json")))

    @property
    def test_source_sha256(self) -> str:
        return canonical_content_id(
            {
                "files": [
                    {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
                    for item in self.test_files
                ]
            },
            excluded=frozenset(),
        )

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @classmethod
    def from_bytes(cls, data: bytes) -> ControlledTaskAuthority:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise ContractError("controlled task authority must be one JSON object")
        value = cls.model_validate(payload)
        if value.canonical_bytes() != data:
            raise ContractError("controlled task authority is not canonical JSON")
        return value


def materialize_controlled_task(
    payload_without_identity: Mapping[str, object],
) -> ControlledTaskAuthority:
    if "authority_id" in payload_without_identity:
        raise ContractError("controlled task materialization input must omit authority_id")
    payload = dict(payload_without_identity)
    payload["authority_id"] = canonical_content_id(payload, excluded=frozenset())
    return ControlledTaskAuthority.model_validate(payload)


def _case(
    *,
    case_id: str,
    case_version: str,
    prompt: str,
    failure_code: str,
    allowed_paths: tuple[str, ...],
    protected_paths: tuple[str, ...],
    test_command: tuple[str, ...],
    test_source_sha256: str,
) -> Case:
    return Case(
        case_id=case_id,
        case_version=case_version,
        case_set="synthetic-python-repair",
        input=CaseInput(
            prompt=prompt,
            parameters={
                "allowed_paths": list(allowed_paths),
                "failure_code": failure_code,
                "protected_paths": list(protected_paths),
                "test_command": list(test_command),
                "test_source_sha256": test_source_sha256,
            },
        ),
        declared_capabilities=("offline-authoritative-repair-receipt",),
        fixture_references=(
            FixtureReference(
                fixture_id=f"{case_id}-tests",
                path="tests/verify.py",
                sha256=test_source_sha256,
            ),
        ),
        tags=("coding", "repair", "deterministic"),
    )


def load_visible_task(case_root: Path) -> ControlledTaskAuthority:
    """Decode one checked-in visible fixture without trusting its declared identity."""

    files = closed_regular_tree(case_root)
    if set(files) != {"baseline.py", "case.json", "solution.py", "verify.py"}:
        raise ContractError("visible Case tree does not match the frozen four-file shape")
    manifest = load_json_bytes(read_regular_file_bytes(files["case.json"]))
    if not isinstance(manifest, dict) or set(manifest) != {
        "case_id",
        "failure_code",
        "split_id",
        "target",
    }:
        raise ContractError("visible Case manifest has an invalid member set")
    case_id = manifest["case_id"]
    failure_code = manifest["failure_code"]
    split_id = manifest["split_id"]
    target = manifest["target"]
    if not all(isinstance(item, str) for item in (case_id, failure_code, split_id, target)):
        raise ContractError("visible Case manifest fields must be strings")
    allowed = (f"src/{target}",)
    protected = ("tests/verify.py",)
    command = ("python", "tests/verify.py", allowed[0])
    workspace = (_task_file(allowed[0], read_regular_file_bytes(files["baseline.py"])),)
    tests = (_task_file(protected[0], read_regular_file_bytes(files["verify.py"])),)
    test_digest = canonical_content_id(
        {
            "files": [
                {
                    "path": tests[0].path,
                    "sha256": tests[0].sha256,
                    "size_bytes": tests[0].size_bytes,
                }
            ]
        },
        excluded=frozenset(),
    )
    case = _case(
        case_id=case_id,
        case_version="1",
        prompt=f"Repair {target} so the frozen verifier passes.",
        failure_code=failure_code,
        allowed_paths=allowed,
        protected_paths=protected,
        test_command=command,
        test_source_sha256=test_digest,
    )
    return materialize_controlled_task(
        {
            "schema_version": "cernora.reference.controlled-task-authority/v1",
            "case": case.model_dump(mode="python"),
            "split_id": split_id,
            "failure_code": failure_code,
            "workspace_files": [item.model_dump(mode="json") for item in workspace],
            "test_files": [item.model_dump(mode="json") for item in tests],
            "allowed_paths": list(allowed),
            "protected_paths": list(protected),
            "test_command": list(command),
        }
    )


def task_from_revealed_case(case: HeldoutArchiveCase) -> ControlledTaskAuthority:
    """Strictly decode a revealed generic Case after custody verification."""

    payload = {
        "schema_version": "cernora.reference.controlled-task-authority/v1",
        "case": case.task.get("case"),
        "split_id": "held-out",
        "failure_code": case.evaluation.get("failure_code"),
        "workspace_files": case.workspace.get("files"),
        "test_files": case.evaluation.get("files"),
        "allowed_paths": case.task.get("allowed_paths"),
        "protected_paths": case.task.get("protected_paths"),
        "test_command": case.evaluation.get("command"),
    }
    try:
        return materialize_controlled_task(payload)
    except (TypeError, ValueError) as exc:
        raise ContractError("revealed Case does not satisfy controlled task authority v1") from exc


__all__ = [
    "MAX_TASK_FILE_BYTES",
    "ControlledTaskAuthority",
    "ControlledTaskFile",
    "load_visible_task",
    "materialize_controlled_task",
    "task_from_revealed_case",
]
