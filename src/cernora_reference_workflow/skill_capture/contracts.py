"""One explicitly configured pi attempt, not another experiment scheduler."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    load_json_file,
    read_regular_file_bytes,
    validate_relative_path,
)
from cernora_reference_workflow.experiment_spec import StrictContract


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class SkillContract(StrictContract):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Snapshot(SkillContract):
    object_id: str = Field(min_length=1)
    record: dict[str, Any]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_pointer: str
    projection_version: str = Field(min_length=1)


class Fact(SkillContract):
    field: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    pointer: str
    expected: Any


class Dependency(SkillContract):
    first_id: str = Field(min_length=1)
    pointer: str
    next_id: str = Field(min_length=1)


class SkillPlan(SkillContract):
    schema_version: Literal["cernora.reference.skill-plan/v1"]
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    extension_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_version: Literal["0.85.1"]
    provider: Literal["deepseek"]
    model: str = Field(min_length=1)
    base_url: Literal["https://api.deepseek.com"]
    thinking: Literal["off", "minimal", "low", "medium", "high"]
    skill_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    skill_version: str = Field(min_length=1)
    skill_files: dict[str, str]
    invocation: Literal["explicit", "implicit"]
    system: str = Field(min_length=1)
    task: str = Field(min_length=1)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    command_path: tuple[str, ...] = Field(min_length=1)
    id_option: str = Field(pattern=r"^--[a-z-]+$")
    objects: tuple[Snapshot, ...] = Field(min_length=1)
    facts: tuple[Fact, ...] = Field(min_length=1)
    dependency: Dependency | None
    max_requests: int = Field(ge=1, le=8)
    max_tool_calls: int = Field(ge=1, le=12)
    max_output_tokens: int = Field(ge=1, le=8192)
    max_request_bytes: int = Field(ge=1, le=262144)
    timeout_seconds: int = Field(ge=1, le=180)
    retries: Literal[0]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if not self.skill_files.get("SKILL.md"):
            raise ValueError("Skill must contain SKILL.md")
        for name, text in self.skill_files.items():
            validate_relative_path(name)
            if len(text.encode()) > 1024 * 1024:
                raise ValueError("Skill resource too large")
        ids = [o.object_id for o in self.objects]
        fields = [f.field for f in self.facts]
        if len(ids) != len(set(ids)) or len(fields) != len(set(fields)):
            raise ValueError("duplicate object or fact")
        index = {o.object_id: o for o in self.objects}
        for fact in self.facts:
            if fact.object_id not in index or canonical_json_bytes(
                resolve_pointer(index[fact.object_id].record, fact.pointer)
            ) != canonical_json_bytes(fact.expected):
                raise ValueError("reference disagrees with frozen snapshot")
        if self.dependency is not None:
            dep = self.dependency
            if dep.first_id == dep.next_id or dep.next_id not in index or dep.first_id not in index:
                raise ValueError("invalid dependency identities")
            if resolve_pointer(index[dep.first_id].record, dep.pointer) != dep.next_id:
                raise ValueError("dependency is not supported by snapshot")
        if self.tool_name == "read":
            raise ValueError("business tool cannot shadow Skill read")
        return self

    @property
    def sha256(self) -> str:
        return digest(canonical_json_bytes(self.model_dump(mode="json")))

    @classmethod
    def read(cls, path: Path) -> SkillPlan:
        value = load_json_file(path)
        return cls.model_validate_json(canonical_json_bytes(value))


def resolve_pointer(value: Any, pointer: str) -> Any:
    if not pointer:
        return value
    if not pointer.startswith("/"):
        raise ContractError("invalid pointer")
    for part in pointer[1:].split("/"):
        if "~" in part.replace("~0", "").replace("~1", ""):
            raise ContractError("invalid pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value[key]
        elif isinstance(value, list) and key.isdecimal() and str(int(key)) == key:
            value = value[int(key)]
        else:
            raise ContractError("pointer unavailable")
    return value


def replay(plan: SkillPlan, argv: list[str]) -> dict[str, Any]:
    """Finite frozen CLI: real argument parsing, help, multiple objects, and errors."""
    if len(argv) > 32 or any(len(a) > 256 or "\n" in a or "\r" in a for a in argv):
        return {"exit_code": 2, "object_id": None, "stdout": {"error": "invalid argv"}}
    path = list(plan.command_path)
    help_text = " ".join([plan.tool_name, *path, plan.id_option, "<id>", "--output json"])
    if argv in ([], ["--help"], ["help"]) or (
        argv and argv[-1] == "--help" and argv[:-1] == path[: len(argv) - 1]
    ):
        return {
            "exit_code": 0,
            "object_id": None,
            "stdout": {
                "usage": help_text,
                "scope": "frozen snapshot replay; read-only; not a live service",
            },
        }
    if argv[: len(path)] != path:
        return {
            "exit_code": 2,
            "object_id": None,
            "stdout": {"error": "unknown command", "usage": help_text},
        }
    args = argv[len(path) :]
    options: dict[str, str] = {}
    while args:
        if len(args) < 2:
            return {"exit_code": 2, "object_id": None, "stdout": {"error": "missing option value"}}
        key, value, *args = args
        key = "--output" if key == "-o" else key
        if key not in {plan.id_option, "--output"} or key in options:
            return {
                "exit_code": 2,
                "object_id": None,
                "stdout": {"error": "unknown or repeated option", "usage": help_text},
            }
        options[key] = value
    if set(options) != {plan.id_option, "--output"} or options["--output"] != "json":
        return {
            "exit_code": 2,
            "object_id": None,
            "stdout": {"error": "required options missing", "usage": help_text},
        }
    match = next((o for o in plan.objects if o.object_id == options[plan.id_option]), None)
    if match is None:
        return {
            "exit_code": 1,
            "object_id": None,
            "stdout": {"error": "object unavailable in frozen snapshot"},
        }
    return {"exit_code": 0, "object_id": match.object_id, "stdout": match.record}


def verify_export(root: Path) -> tuple[SkillPlan, dict[str, bytes]]:
    manifest = load_json_file(root / "manifest.json")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "plan_sha256", "files"}
        or manifest["schema_version"] != "cernora.reference.skill-export/v1"
    ):
        raise ContractError("invalid Skill export manifest")
    files = manifest["files"]
    if not isinstance(files, dict) or set(files) != {
        "plan.json",
        "events.jsonl",
        "requests.jsonl",
        "tools.jsonl",
        "process.json",
    }:
        raise ContractError("unexpected Skill export files")
    actual = set(closed_regular_tree(root))
    if actual != set(files) | {"manifest.json"}:
        raise ContractError("Skill export is not a closed tree")
    payloads = {name: read_regular_file_bytes(root / name) for name in files}
    if any(digest(payloads[name]) != expected for name, expected in files.items()):
        raise ContractError("Skill export digest mismatch")
    plan = SkillPlan.model_validate_json(payloads["plan.json"])
    if plan.sha256 != manifest["plan_sha256"]:
        raise ContractError("Skill plan identity mismatch")
    # Strictly parse before any observer uses the native rows.
    for name in ("events.jsonl", "requests.jsonl", "tools.jsonl"):
        for line in payloads[name].splitlines():
            load_json_bytes(line)
    load_json_bytes(payloads["process.json"])
    return plan, payloads
