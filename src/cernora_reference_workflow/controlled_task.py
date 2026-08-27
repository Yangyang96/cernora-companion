"""Generic, content-identified task authority for controlled repair Cases."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Self

from cernora import Case, CaseInput, FixtureReference
from pydantic import Field, JsonValue, StrictStr, field_validator, model_validator

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

if TYPE_CHECKING:
    from cernora_reference_workflow.heldout_seal import HeldoutArchiveCase

MAX_TASK_FILE_BYTES = 2 * 1024 * 1024


class _HeldoutTaskProjection(StrictV2Contract):
    schema_version: Literal["cernora.reference.heldout-task/v1"]
    language: Literal["python"]
    instruction: StrictStr = Field(min_length=1)
    allowed_paths: tuple[StrictStr, ...]
    protected_paths: tuple[StrictStr, ...]
    case_version: Literal["1"]

    @field_validator("allowed_paths", "protected_paths", mode="before")
    @classmethod
    def tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class _HeldoutWorkspaceFile(StrictV2Contract):
    path: StrictStr = Field(min_length=1)
    content_utf8: StrictStr


class _HeldoutWorkspaceProjection(StrictV2Contract):
    schema_version: Literal["cernora.reference.heldout-workspace/v1"]
    files: Annotated[tuple[_HeldoutWorkspaceFile, ...], Field(min_length=1)]

    @field_validator("files", mode="before")
    @classmethod
    def tuple_files(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class _HeldoutEvaluationProjection(StrictV2Contract):
    schema_version: Literal["cernora.reference.heldout-evaluation/v1"]
    command: Annotated[tuple[StrictStr, ...], Field(min_length=1)]
    working_directory: Literal["."]
    timeout_seconds: Literal[60]
    network: Literal["disabled"]
    expected_exit_code: Literal[0]
    success_metric: Literal["verifier_exit_zero"]
    failure_codes: Annotated[tuple[Identifier, ...], Field(min_length=1)]

    @field_validator("command", "failure_codes", mode="before")
    @classmethod
    def tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("timeout_seconds", "expected_exit_code", mode="before")
    @classmethod
    def strict_integers(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("held-out evaluation integer fields must be strict integers")
        return value


class _HeldoutProjectionBinding(StrictV2Contract):
    task_schema_version: Literal["cernora.reference.heldout-task/v1"]
    language: Literal["python"]
    case_version: Literal["1"]
    workspace_schema_version: Literal["cernora.reference.heldout-workspace/v1"]
    workspace_file_order: tuple[StrictStr, ...]
    allowed_path_order: tuple[StrictStr, ...]
    protected_path_order: tuple[StrictStr, ...]
    evaluation_schema_version: Literal["cernora.reference.heldout-evaluation/v1"]
    working_directory: Literal["."]
    timeout_seconds: Literal[60]
    network: Literal["disabled"]
    expected_exit_code: Literal[0]
    success_metric: Literal["verifier_exit_zero"]
    failure_codes: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1)]

    @field_validator(
        "workspace_file_order",
        "allowed_path_order",
        "protected_path_order",
        "failure_codes",
        mode="before",
    )
    @classmethod
    def tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("timeout_seconds", "expected_exit_code", mode="before")
    @classmethod
    def strict_integers(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("held-out binding integer fields must be strict integers")
        return value


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
        expected: dict[str, object] = {
            "allowed_paths": list(self.allowed_paths),
            "failure_code": self.failure_code,
            "protected_paths": list(self.protected_paths),
            "test_command": list(self.test_command),
            "test_source_sha256": self.test_source_sha256,
        }
        projection = parameters.get("heldout_archive_v1")
        if projection is not None:
            if self.split_id != "held-out":
                raise ValueError("held-out projection binding requires the held-out split")
            binding = _HeldoutProjectionBinding.model_validate(projection)
            if (
                tuple(item.path for item in self.workspace_files) != self.allowed_paths
                or tuple(item.path for item in self.test_files) != self.protected_paths
            ):
                raise ValueError(
                    "held-out files do not match their allowed/protected classification"
                )
            if binding.failure_codes != (self.failure_code,):
                raise ValueError("held-out failure synthesis is not canonical")
            expected_fixtures = tuple(
                FixtureReference(
                    fixture_id=f"{self.case.case_id}-protected-{index}",
                    path=item.path,
                    sha256=item.sha256,
                )
                for index, item in enumerate(self.test_files, start=1)
            )
            if (
                self.case.case_set != "synthetic-python-repair"
                or self.case.case_version != binding.case_version
                or self.case.declared_capabilities != ("offline-authoritative-repair-receipt",)
                or self.case.fixture_references != expected_fixtures
                or self.case.tags != ("coding", "repair", "deterministic")
            ):
                raise ValueError("held-out Core Case synthesis is not canonical")
            expected["heldout_archive_v1"] = binding.model_dump(mode="json")
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
    def authority_sha256(self) -> str:
        return sha256_bytes(self.canonical_bytes())

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
    try:
        task = _HeldoutTaskProjection.model_validate(case.task)
        workspace = _HeldoutWorkspaceProjection.model_validate(case.workspace)
        evaluation = _HeldoutEvaluationProjection.model_validate(case.evaluation)
        allowed = tuple(sorted(task.allowed_paths))
        protected = tuple(sorted(task.protected_paths))
        if len(set(task.allowed_paths)) != len(task.allowed_paths) or len(
            set(task.protected_paths)
        ) != len(task.protected_paths):
            raise ContractError("revealed Case path policies must be unique")
        for path in (*allowed, *protected):
            validate_relative_path(path)
        if set(allowed).intersection(protected):
            raise ContractError("revealed Case path policies must be disjoint")
        file_by_path: dict[str, ControlledTaskFile] = {}
        for item in workspace.files:
            validate_relative_path(item.path)
            if item.path in file_by_path:
                raise ContractError("revealed Case workspace paths must be unique")
            file_by_path[item.path] = _task_file(item.path, item.content_utf8.encode("utf-8"))
        if set(file_by_path) != set(allowed).union(protected):
            raise ContractError("revealed Case files must exactly equal its path policy")
        if len(evaluation.failure_codes) != 1:
            raise ContractError("revealed Case must declare exactly one versioned failure code")
        failure_code = evaluation.failure_codes[0]
        workspace_files = tuple(file_by_path[path] for path in allowed)
        test_files = tuple(file_by_path[path] for path in protected)
        test_digest = canonical_content_id(
            {
                "files": [
                    {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
                    for item in test_files
                ]
            },
            excluded=frozenset(),
        )
        source_binding = _HeldoutProjectionBinding(
            task_schema_version=task.schema_version,
            language=task.language,
            case_version=task.case_version,
            workspace_schema_version=workspace.schema_version,
            workspace_file_order=tuple(item.path for item in workspace.files),
            allowed_path_order=task.allowed_paths,
            protected_path_order=task.protected_paths,
            evaluation_schema_version=evaluation.schema_version,
            working_directory=evaluation.working_directory,
            timeout_seconds=evaluation.timeout_seconds,
            network=evaluation.network,
            expected_exit_code=evaluation.expected_exit_code,
            success_metric=evaluation.success_metric,
            failure_codes=evaluation.failure_codes,
        )
        parameters = {
            "allowed_paths": list(allowed),
            "failure_code": failure_code,
            "protected_paths": list(protected),
            "test_command": list(evaluation.command),
            "test_source_sha256": test_digest,
            "heldout_archive_v1": source_binding.model_dump(mode="json"),
        }
        core_case = Case(
            case_id=case.case_id,
            case_version=task.case_version,
            case_set="synthetic-python-repair",
            input=CaseInput(prompt=task.instruction, parameters=parameters),
            declared_capabilities=("offline-authoritative-repair-receipt",),
            fixture_references=tuple(
                FixtureReference(
                    fixture_id=f"{case.case_id}-protected-{index}",
                    path=item.path,
                    sha256=item.sha256,
                )
                for index, item in enumerate(test_files, start=1)
            ),
            tags=("coding", "repair", "deterministic"),
        )
        authority = materialize_controlled_task(
            {
                "schema_version": "cernora.reference.controlled-task-authority/v1",
                "case": core_case.model_dump(mode="python"),
                "split_id": "held-out",
                "failure_code": failure_code,
                "workspace_files": [item.model_dump(mode="json") for item in workspace_files],
                "test_files": [item.model_dump(mode="json") for item in test_files],
                "allowed_paths": list(allowed),
                "protected_paths": list(protected),
                "test_command": list(evaluation.command),
            }
        )
    except (TypeError, ValueError) as exc:
        raise ContractError("revealed Case does not satisfy controlled task authority v1") from exc
    if canonical_json_bytes(
        reconstructed_revealed_case(authority).model_dump(mode="json")
    ) != canonical_json_bytes(case.model_dump(mode="json")):
        raise ContractError("revealed Case conversion is not byte-exact and reversible")
    return authority


def reconstructed_revealed_case(task: ControlledTaskAuthority) -> HeldoutArchiveCase:
    """Rebuild the exact accepted generic v1 Case projection from one task authority."""

    from cernora_reference_workflow.heldout_seal import HeldoutArchiveCase

    try:
        if task.split_id != "held-out":
            raise ContractError("only held-out task authorities have a reveal projection")
        binding = _HeldoutProjectionBinding.model_validate(
            task.case.input.parameters.get("heldout_archive_v1")
        )
        file_by_path = {item.path: item for item in (*task.workspace_files, *task.test_files)}
        if (
            len(binding.workspace_file_order) != len(set(binding.workspace_file_order))
            or len(file_by_path) != len(task.workspace_files) + len(task.test_files)
            or set(binding.workspace_file_order) != set(file_by_path)
        ):
            raise ContractError("task authority does not bind the revealed file order")
        if (
            len(binding.allowed_path_order) != len(set(binding.allowed_path_order))
            or len(binding.protected_path_order) != len(set(binding.protected_path_order))
            or set(binding.allowed_path_order) != set(task.allowed_paths)
            or set(binding.protected_path_order) != set(task.protected_paths)
        ):
            raise ContractError("task authority path order contradicts the revealed workspace")
        workspace_files: list[JsonValue] = []
        for path in binding.workspace_file_order:
            try:
                content = file_by_path[path].content().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ContractError("revealed workspace file is not valid UTF-8") from exc
            workspace_files.append({"path": path, "content_utf8": content})
        return HeldoutArchiveCase(
            case_id=task.case.case_id,
            task={
                "schema_version": binding.task_schema_version,
                "language": binding.language,
                "instruction": task.case.input.prompt,
                "allowed_paths": list(binding.allowed_path_order),
                "protected_paths": list(binding.protected_path_order),
                "case_version": binding.case_version,
            },
            workspace={
                "schema_version": binding.workspace_schema_version,
                "files": workspace_files,
            },
            evaluation={
                "schema_version": binding.evaluation_schema_version,
                "command": list(task.test_command),
                "working_directory": binding.working_directory,
                "timeout_seconds": binding.timeout_seconds,
                "network": binding.network,
                "expected_exit_code": binding.expected_exit_code,
                "success_metric": binding.success_metric,
                "failure_codes": list(binding.failure_codes),
            },
        )
    except (TypeError, ValueError) as exc:
        raise ContractError("task authority has no exact generic reveal projection") from exc


__all__ = [
    "MAX_TASK_FILE_BYTES",
    "ControlledTaskAuthority",
    "ControlledTaskFile",
    "load_visible_task",
    "materialize_controlled_task",
    "reconstructed_revealed_case",
    "task_from_revealed_case",
]
