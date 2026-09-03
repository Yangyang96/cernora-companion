"""One-shot, development-only Runtime diagnostic pilot control plane."""

from __future__ import annotations

import fcntl
import importlib.metadata
import os
import secrets
import shutil
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptExecutor,
    ControlledAttemptRequest,
    VerifiedControlledAttemptArtifact,
    publish_controlled_attempt_artifact,
    verify_controlled_attempt_artifact,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    ControlledExperimentSpecV2,
    Digest,
    StrictV2Contract,
)
from cernora_reference_workflow.controlled_run_plan import ControlledTrialSlotV2
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.development_agent_pilot import DevelopmentAgentPilotPlan
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.study_preparation import (
    ImplementationCandidate,
    ImplementationName,
    _candidate,
)

DIAGNOSTIC_CASE_ID = "p4-dev-json-pointer"
DIAGNOSTIC_AGENT_TIMEOUT_SECONDS = 300
DIAGNOSTIC_ATTEMPT_ENVELOPE_SECONDS = 360
DIAGNOSTIC_MAX_WALL_SECONDS = 900
DIAGNOSTIC_PREFLIGHT_FREE_BYTES = 15 * 1024**3
DIAGNOSTIC_SAFE_STOP_FREE_BYTES = 8 * 1024**3
SOURCE_PI_DEVELOPMENT_PLAN_ID = "e490d58fb7500d77019a5ca566809e0d7f0834a46597ca0989540e6d4cca9458"
CONSUMED_DIAGNOSTIC_PLAN_ID = "6a342640911cade0ed3bd381e3ff80e0327d5230817a72ef6bac5d46e8d8bd4a"
CONSUMED_VALUE_FREE_DIAGNOSTIC_PLAN_ID = (
    "b039fa42eafc1a85be6e79bbbb4952639f64d8b4b89d3f62184b68838058ff76"
)

WallClock = Callable[[], float]
Clock = Callable[[], float]
DiskProbe = Callable[[Path], int]
DiagnosticCode = Literal[
    "agent-timeout-agent-result",
    "agent-timeout-agent-timing-shape",
    "agent-timeout-duration-bound",
    "agent-timeout-evidence-accepted",
    "agent-timeout-exception-timezone",
    "agent-timeout-message",
    "agent-timeout-timestamp-parse",
    "agent-timeout-timezone-order",
    "agent-timeout-traceback",
    "agent-timeout-verifier-result",
    "agent-timeout-verifier-timing-shape",
    "infrastructure-start-exception",
    "job-config-authority-rejected",
    "missing-trial-result",
    "non-timeout-exception-with-phase-evidence",
    "process-envelope-failure",
    "preterminal-structure-rejected",
    "strict-runtime-evidence-rejected",
    "trial-config-authority-rejected",
    "trial-tree-rejected",
    "transient-provider-exception",
    "unclassified-runtime-exception",
]


class RuntimeDiagnosticPilotPlan(StrictV2Contract):
    """Exact one-Case, one-Attempt authority proposed for diagnosis."""

    schema_version: Literal["cernora.reference.runtime-diagnostic-pilot-plan/v1"]
    plan_id: Digest
    status: Literal["awaiting-user-authorization"]
    execution_authorized: Literal[False]
    authority_scope: Literal["development-only-runtime-diagnostic"]
    source_development_plan_id: Literal[
        "e490d58fb7500d77019a5ca566809e0d7f0834a46597ca0989540e6d4cca9458"
    ]
    task: ControlledTaskAuthority
    specification: ControlledExperimentSpecV2
    implementation_candidates: tuple[ImplementationCandidate, ...]
    repetitions: Literal[1]
    planned_trial_count: Literal[1]
    maximum_attempt_count: Literal[1]
    concurrency: Literal[1]
    agent_timeout_seconds: Literal[300]
    attempt_envelope_timeout_seconds: Literal[360]
    maximum_wall_seconds: Literal[900]
    preflight_free_bytes: Literal[16106127360]
    safe_stop_free_bytes: Literal[8589934592]
    external_provider_scope: Literal["openai-codex-authenticated-generation-only"]
    completion: Literal["stop-after-one-terminal-publication"]
    no_retry: Literal[True]
    claim_authority: Literal["diagnostic-only"]
    prohibited_actions: tuple[
        Literal[
            "candidate-construction",
            "held-out-access",
            "held-out-reveal",
            "smoke-execution",
            "study-start-execution",
            "study-step-execution",
            "54-trial-matrix",
        ],
        ...,
    ]

    @field_validator("implementation_candidates", "prohibited_actions", mode="before")
    @classmethod
    def tuple_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_one_shot_authority(self) -> Self:
        expected_prohibitions = (
            "candidate-construction",
            "held-out-access",
            "held-out-reveal",
            "smoke-execution",
            "study-start-execution",
            "study-step-execution",
            "54-trial-matrix",
        )
        if (
            self.task.case.case_id != DIAGNOSTIC_CASE_ID
            or self.task.split_id != "development"
            or self.specification.task.task_id != DIAGNOSTIC_CASE_ID
            or self.specification.configuration_id != "baseline"
            or self.specification.task.authority_id != self.task.authority_id
            or self.specification.task.authority_sha256 != self.task.authority_sha256
            or self.specification.task.authority_source.payload != self.task.model_dump(mode="json")
            or self.specification.limits.timeout_seconds != DIAGNOSTIC_AGENT_TIMEOUT_SECONDS
            or tuple(item.name for item in self.implementation_candidates)
            != ("cernora", "cernora-reference-workflow")
            or self.prohibited_actions != expected_prohibitions
        ):
            raise ValueError("Runtime diagnostic pilot authority is not exact")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"plan_id"})
        )
        if self.plan_id != expected:
            raise ValueError("Runtime diagnostic pilot Plan identity mismatch")
        return self

    @classmethod
    def from_bytes(cls, data: bytes) -> RuntimeDiagnosticPilotPlan:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise ContractError("Runtime diagnostic pilot Plan must be one JSON object")
        plan = cls.model_validate(payload)
        if data != plan.canonical_bytes():
            raise ContractError("Runtime diagnostic pilot Plan is not canonical JSON")
        return plan

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def trial_slot(self) -> ControlledTrialSlotV2:
        identity = {
            "case_id": DIAGNOSTIC_CASE_ID,
            "configuration_id": "baseline",
            "experiment_id": self.specification.experiment_id,
            "repetition": 1,
            "run_plan_id": self.plan_id,
        }
        return ControlledTrialSlotV2(
            schema_version="cernora.reference.controlled-trial-slot/v2",
            trial_slot_id=canonical_content_id(identity, excluded=frozenset()),
            run_plan_id=self.plan_id,
            slot_index=1,
            case_id=DIAGNOSTIC_CASE_ID,
            configuration_id="baseline",
            experiment_id=self.specification.experiment_id,
            repetition=1,
        )


class RuntimeDiagnosticAuthorizationRequest(StrictV2Contract):
    """Exact request awaiting a user decision; never an acceptance token."""

    schema_version: Literal["cernora.reference.runtime-diagnostic-authorization-request/v1"]
    request_id: Digest
    status: Literal["awaiting-user-authorization"]
    plan_id: Digest
    action: Literal["one-development-runtime-diagnostic-attempt"]
    case_id: Literal["p4-dev-json-pointer"]
    case_authority_sha256: Digest
    maximum_attempt_count: Literal[1]
    concurrency: Literal[1]
    agent_timeout_seconds: Literal[300]
    attempt_envelope_timeout_seconds: Literal[360]
    maximum_wall_seconds: Literal[900]
    no_retry: Literal[True]
    external_provider_scope: Literal["openai-codex-authenticated-generation-only"]
    custody_path_sha256: Digest
    completion: Literal["stop-after-one-terminal-publication"]
    explicitly_not_authorized: tuple[
        Literal[
            "candidate-construction",
            "held-out-access",
            "held-out-reveal",
            "second-attempt",
            "smoke-execution",
            "study-execution",
        ],
        ...,
    ]

    @field_validator("explicitly_not_authorized", mode="before")
    @classmethod
    def tuple_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_request(self) -> Self:
        expected_not_authorized = (
            "candidate-construction",
            "held-out-access",
            "held-out-reveal",
            "second-attempt",
            "smoke-execution",
            "study-execution",
        )
        if self.explicitly_not_authorized != expected_not_authorized:
            raise ValueError("Runtime diagnostic authorization exclusions drifted")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"request_id"})
        )
        if self.request_id != expected:
            raise ValueError("Runtime diagnostic authorization request identity mismatch")
        return self

    @classmethod
    def from_bytes(cls, data: bytes) -> RuntimeDiagnosticAuthorizationRequest:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise ContractError("Runtime diagnostic request must be one JSON object")
        request = cls.model_validate(payload)
        if data != request.canonical_bytes():
            raise ContractError("Runtime diagnostic request is not canonical JSON")
        return request

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class RuntimeDiagnosticExecutionRecord(StrictV2Contract):
    schema_version: Literal["cernora.reference.runtime-diagnostic-execution/v1"]
    execution_id: Digest
    plan_id: Digest
    authorization_request_id: Digest
    custody_path_sha256: Digest
    nonce: Digest
    prepared_unix_milliseconds: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def canonical_execution(self) -> Self:
        expected = canonical_content_id(
            {
                "authorization_request_id": self.authorization_request_id,
                "custody_path_sha256": self.custody_path_sha256,
                "nonce": self.nonce,
                "plan_id": self.plan_id,
            },
            excluded=frozenset(),
        )
        if self.execution_id != expected:
            raise ValueError("Runtime diagnostic execution identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class RuntimeDiagnosticClaim(StrictV2Contract):
    schema_version: Literal["cernora.reference.runtime-diagnostic-claim/v1"]
    claim_id: Digest
    execution_id: Digest
    trial_id: Digest
    ordinal: Literal[1]
    predecessor_attempt_id: None
    observed_unix_milliseconds: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def canonical_claim(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"claim_id"})
        )
        if self.claim_id != expected:
            raise ValueError("Runtime diagnostic claim identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class RuntimeDiagnosticIncident(StrictV2Contract):
    schema_version: Literal["cernora.reference.runtime-diagnostic-incident/v1"]
    incident_id: Digest
    execution_id: Digest
    claim_id: Digest
    phase: Literal["executor", "attempt-validation", "artifact-publication"]
    category: Literal["ambiguous-one-shot-attempt"]
    observed_unix_milliseconds: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def canonical_incident(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"incident_id"})
        )
        if self.incident_id != expected:
            raise ValueError("Runtime diagnostic incident identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class RuntimeDiagnosticOutcome(StrictV2Contract):
    schema_version: Literal["cernora.reference.runtime-diagnostic-outcome/v1"]
    outcome_id: Digest
    execution_id: Digest
    plan_id: Digest
    claim_id: Digest
    attempt_id: Digest
    attempt_artifact_id: Digest
    classification: Literal["timed-out", "evaluated", "inconclusive"]
    diagnostic_code: DiagnosticCode | None = None
    terminal_evidence: Literal["controlled-attempt-terminal"]
    claim_authority: Literal["diagnostic-only"]
    no_retry: Literal[True]
    completed_unix_milliseconds: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def canonical_outcome(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json", exclude_none=True),
            excluded=frozenset({"outcome_id"}),
        )
        if self.outcome_id != expected:
            raise ValueError("Runtime diagnostic outcome identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json", exclude_none=True))


class RuntimeDiagnosticReceipt(StrictV2Contract):
    """Value-free reason code retained before terminal artifact publication."""

    schema_version: Literal["cernora.reference.runtime-diagnostic-receipt/v1"]
    receipt_id: Digest
    execution_id: Digest
    claim_id: Digest
    diagnostic_code: DiagnosticCode

    @model_validator(mode="after")
    def canonical_receipt(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"receipt_id"})
        )
        if self.receipt_id != expected:
            raise ValueError("Runtime diagnostic receipt identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


@dataclass(frozen=True)
class RuntimeDiagnosticState:
    plan: RuntimeDiagnosticPilotPlan
    request: RuntimeDiagnosticAuthorizationRequest
    record: RuntimeDiagnosticExecutionRecord
    claim: RuntimeDiagnosticClaim | None
    artifact: VerifiedControlledAttemptArtifact | None
    outcome: RuntimeDiagnosticOutcome | None
    incident: RuntimeDiagnosticIncident | None
    diagnostic: RuntimeDiagnosticReceipt | None

    @property
    def status(self) -> Literal["prepared", "ambiguous", "adoptable", "completed"]:
        if self.outcome is not None:
            return "completed"
        if self.artifact is not None:
            return "adoptable"
        if self.claim is not None:
            return "ambiguous"
        return "prepared"


class RuntimeDiagnosticStopped(RuntimeError):
    """A frozen safety bound stopped the diagnostic before completion."""


class AmbiguousRuntimeDiagnosticAttempt(RuntimeError):
    """The only authorized Attempt was claimed but cannot be rerun."""


class _RecoveryOnlyExecutor:
    """Sentinel proving that replay/adoption cannot initiate an external Attempt."""

    @property
    def enforces_hard_deadline(self) -> bool:
        return False

    def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
        del request
        raise AssertionError("recovery-only executor must never be called")


def build_runtime_diagnostic_pilot_plan(
    source: DevelopmentAgentPilotPlan,
    *,
    implementation_candidates: tuple[ImplementationCandidate, ...],
) -> RuntimeDiagnosticPilotPlan:
    source = DevelopmentAgentPilotPlan.from_bytes(source.canonical_bytes())
    if source.plan_id != SOURCE_PI_DEVELOPMENT_PLAN_ID:
        raise ContractError("Runtime diagnostic source is not the pinned development Plan")
    indexed_tasks = {item.case.case_id: item for item in source.corpus.tasks}
    indexed_specs = {item.task.task_id: item for item in source.experiment_specs}
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.runtime-diagnostic-pilot-plan/v1",
        "status": "awaiting-user-authorization",
        "execution_authorized": False,
        "authority_scope": "development-only-runtime-diagnostic",
        "source_development_plan_id": source.plan_id,
        "task": indexed_tasks[DIAGNOSTIC_CASE_ID].model_dump(mode="json"),
        "specification": indexed_specs[DIAGNOSTIC_CASE_ID].model_dump(mode="json"),
        "implementation_candidates": [
            item.model_dump(mode="json") for item in implementation_candidates
        ],
        "repetitions": 1,
        "planned_trial_count": 1,
        "maximum_attempt_count": 1,
        "concurrency": 1,
        "agent_timeout_seconds": DIAGNOSTIC_AGENT_TIMEOUT_SECONDS,
        "attempt_envelope_timeout_seconds": DIAGNOSTIC_ATTEMPT_ENVELOPE_SECONDS,
        "maximum_wall_seconds": DIAGNOSTIC_MAX_WALL_SECONDS,
        "preflight_free_bytes": DIAGNOSTIC_PREFLIGHT_FREE_BYTES,
        "safe_stop_free_bytes": DIAGNOSTIC_SAFE_STOP_FREE_BYTES,
        "external_provider_scope": "openai-codex-authenticated-generation-only",
        "completion": "stop-after-one-terminal-publication",
        "no_retry": True,
        "claim_authority": "diagnostic-only",
        "prohibited_actions": [
            "candidate-construction",
            "held-out-access",
            "held-out-reveal",
            "smoke-execution",
            "study-start-execution",
            "study-step-execution",
            "54-trial-matrix",
        ],
    }
    payload["plan_id"] = canonical_content_id(payload, excluded=frozenset())
    return RuntimeDiagnosticPilotPlan.model_validate(payload)


def _custody_path_sha256(path: Path, *, must_exist: bool) -> str:
    if not must_exist and (path.exists() or path.is_symlink()):
        raise ContractError("Runtime diagnostic custody destination must be new")
    lexical = Path(os.path.abspath(path))
    resolved = (
        path.resolve(strict=True) if must_exist else path.parent.resolve(strict=True) / path.name
    )
    if path.is_symlink() or resolved != lexical:
        raise ContractError("Runtime diagnostic custody path has non-real ancestry")
    return sha256_bytes(os.fsencode(resolved))


def runtime_diagnostic_custody_path(repository_root: Path, plan_id: str) -> Path:
    """Return the only repository custody path allowed for one diagnostic Plan."""

    repository = repository_root.resolve(strict=True)
    custody_parent = repository / ".agent" / "custody"
    lexical_parent = Path(os.path.abspath(custody_parent))
    if custody_parent.resolve(strict=True) != lexical_parent:
        raise ContractError("Runtime diagnostic custody parent has non-real ancestry")
    return custody_parent / f"runtime-diagnostic-{plan_id}"


def build_runtime_diagnostic_authorization_request(
    plan: RuntimeDiagnosticPilotPlan,
    *,
    custody: Path,
) -> RuntimeDiagnosticAuthorizationRequest:
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.runtime-diagnostic-authorization-request/v1",
        "status": "awaiting-user-authorization",
        "plan_id": plan.plan_id,
        "action": "one-development-runtime-diagnostic-attempt",
        "case_id": DIAGNOSTIC_CASE_ID,
        "case_authority_sha256": plan.task.authority_sha256,
        "maximum_attempt_count": 1,
        "concurrency": 1,
        "agent_timeout_seconds": DIAGNOSTIC_AGENT_TIMEOUT_SECONDS,
        "attempt_envelope_timeout_seconds": DIAGNOSTIC_ATTEMPT_ENVELOPE_SECONDS,
        "maximum_wall_seconds": DIAGNOSTIC_MAX_WALL_SECONDS,
        "no_retry": True,
        "external_provider_scope": "openai-codex-authenticated-generation-only",
        "custody_path_sha256": _custody_path_sha256(custody, must_exist=False),
        "completion": "stop-after-one-terminal-publication",
        "explicitly_not_authorized": [
            "candidate-construction",
            "held-out-access",
            "held-out-reveal",
            "second-attempt",
            "smoke-execution",
            "study-execution",
        ],
    }
    payload["request_id"] = canonical_content_id(payload, excluded=frozenset())
    return RuntimeDiagnosticAuthorizationRequest.model_validate(payload)


def _review_bytes(
    plan: RuntimeDiagnosticPilotPlan,
    request: RuntimeDiagnosticAuthorizationRequest,
) -> bytes:
    if plan.plan_id == CONSUMED_DIAGNOSTIC_PLAN_ID:
        diagnostic_receipt = ""
    elif plan.plan_id == CONSUMED_VALUE_FREE_DIAGNOSTIC_PLAN_ID:
        diagnostic_receipt = (
            "- Diagnostic receipt: one fixed value-free code; no raw Runtime evidence\n"
        )
    else:
        diagnostic_receipt = (
            "- Pre-terminal diagnostic receipt: at most one fixed value-free code; "
            "no raw Runtime evidence\n"
        )
    return (
        "# Priority 4 one-shot Runtime diagnostic authorization request\n\n"
        "Status: **awaiting explicit user authorization; no execution is authorized**\n\n"
        f"- Plan ID: `{plan.plan_id}`\n"
        f"- Request ID: `{request.request_id}`\n"
        f"- Case: `{DIAGNOSTIC_CASE_ID}`\n"
        "- Scope: one development-only Trial, exactly one Attempt, no retry\n"
        "- Bounds: concurrency 1; Agent 300s; Attempt envelope 360s; total wall 900s\n"
        "- Provider: authenticated OpenAI Codex generation only\n"
        "- Output: one diagnostic-only controlled terminal artifact\n"
        f"{diagnostic_receipt}"
        "- Excluded: Candidate, held-out, smoke, Study, matrix, or second Attempt authority\n"
    ).encode()


def create_runtime_diagnostic_proposal(
    destination: Path,
    *,
    source_plan: DevelopmentAgentPilotPlan,
    cernora_wheel: Path,
    companion_wheel: Path,
    repository_root: Path,
) -> tuple[RuntimeDiagnosticPilotPlan, RuntimeDiagnosticAuthorizationRequest]:
    """Publish a closed offline proposal; this operation grants no authority."""

    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ContractError("Runtime diagnostic proposal destination must be new")
    source_candidates = source_plan.implementation_candidates
    if source_candidates is None:
        raise ContractError("Runtime diagnostic source omits implementation versions")
    versions = {item.name: item.version for item in source_candidates}
    candidates = (
        _candidate(
            cernora_wheel,
            expected_name="cernora",
            expected_version=versions["cernora"],
        ),
        _candidate(
            companion_wheel,
            expected_name="cernora-reference-workflow",
            expected_version=versions["cernora-reference-workflow"],
        ),
    )
    plan = build_runtime_diagnostic_pilot_plan(
        source_plan,
        implementation_candidates=candidates,
    )
    custody = runtime_diagnostic_custody_path(repository_root, plan.plan_id)
    request = build_runtime_diagnostic_authorization_request(plan, custody=custody)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    published = False
    try:
        (staging / "plan.json").write_bytes(plan.canonical_bytes())
        (staging / "request.json").write_bytes(request.canonical_bytes())
        (staging / "review.md").write_bytes(_review_bytes(plan, request))
        for path in staging.iterdir():
            path.chmod(0o600)
        atomic_publish_directory(staging, destination)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)
    return inspect_runtime_diagnostic_proposal(destination)


def inspect_runtime_diagnostic_proposal(
    root: Path,
) -> tuple[RuntimeDiagnosticPilotPlan, RuntimeDiagnosticAuthorizationRequest]:
    files = closed_regular_tree(root)
    expected = {"plan.json", "request.json", "review.md"}
    if set(files) != expected or {item.name for item in root.iterdir()} != expected:
        raise ContractError("Runtime diagnostic proposal has unknown or missing files")
    plan = RuntimeDiagnosticPilotPlan.from_bytes(read_regular_file_bytes(files["plan.json"]))
    request = RuntimeDiagnosticAuthorizationRequest.from_bytes(
        read_regular_file_bytes(files["request.json"])
    )
    if (
        request.plan_id != plan.plan_id
        or request.case_authority_sha256 != plan.task.authority_sha256
        or read_regular_file_bytes(files["review.md"]) != _review_bytes(plan, request)
    ):
        raise ContractError("Runtime diagnostic proposal authorities do not close")
    return plan, request


def verify_runtime_diagnostic_runtime(
    plan: RuntimeDiagnosticPilotPlan,
    *,
    repository_root: Path,
    cernora_wheel: Path,
    companion_wheel: Path,
) -> None:
    """Bind the active interpreter to both exact proposed wheel candidates."""

    expected_prefix = repository_root.resolve(strict=True) / ".venv"
    if Path(os.path.realpath(sys.prefix)) != expected_prefix.resolve(strict=True):
        raise ContractError("Runtime diagnostic interpreter is outside repository .venv")
    expected_by_name = {item.name: item for item in plan.implementation_candidates}
    runtime_wheels: tuple[tuple[Path, ImplementationName], ...] = (
        (cernora_wheel, "cernora"),
        (companion_wheel, "cernora-reference-workflow"),
    )
    for wheel, name in runtime_wheels:
        expected = expected_by_name[name]
        actual = _candidate(wheel, expected_name=name, expected_version=expected.version)
        if actual != expected:
            raise ContractError("Runtime diagnostic wheel bytes changed")
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ContractError("Runtime diagnostic distribution is unavailable") from exc
        if distribution.version != expected.version:
            raise ContractError("Runtime diagnostic distribution version changed")
        with zipfile.ZipFile(wheel) as archive:
            members = tuple(
                item
                for item in archive.infolist()
                if not item.is_dir() and not item.filename.endswith(".dist-info/RECORD")
            )
            if not members:
                raise ContractError("Runtime diagnostic wheel is empty")
            for member in members:
                installed = Path(str(distribution.locate_file(member.filename)))
                if read_regular_file_bytes(installed, maximum=None) != archive.read(member):
                    raise ContractError("Runtime diagnostic installed bytes changed")


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("exclusive Runtime diagnostic write did not advance")
            view = view[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        with suppress(OSError):
            path.unlink()
        raise
    finally:
        with suppress(OSError):
            os.close(descriptor)
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_descriptor = os.open(path.parent, parent_flags)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)


def _model(path: Path, model: type[StrictV2Contract]) -> StrictV2Contract:
    raw = read_regular_file_bytes(path)
    payload = load_json_bytes(raw)
    if not isinstance(payload, dict):
        raise ContractError("Runtime diagnostic custody JSON must be one object")
    value = model.model_validate(payload)
    canonical = value.canonical_bytes()  # type: ignore[attr-defined]
    if raw != canonical:
        raise ContractError("Runtime diagnostic custody JSON is not canonical")
    return value


def prepare_runtime_diagnostic_pilot(
    plan: RuntimeDiagnosticPilotPlan,
    request: RuntimeDiagnosticAuthorizationRequest,
    destination: Path,
    *,
    accepted_plan_id: str,
    accepted_request_id: str,
    nonce: str | None = None,
    wall_clock: WallClock = time.time,
    disk_free: DiskProbe = lambda path: shutil.disk_usage(path).free,
) -> RuntimeDiagnosticState:
    custody_sha256 = _custody_path_sha256(destination, must_exist=False)
    if (
        accepted_plan_id != plan.plan_id
        or accepted_request_id != request.request_id
        or request.plan_id != plan.plan_id
        or request.case_authority_sha256 != plan.task.authority_sha256
        or request.custody_path_sha256 != custody_sha256
    ):
        raise ContractError(
            "Runtime diagnostic acceptance and request do not bind this Plan and custody"
        )
    if disk_free(destination.parent) < DIAGNOSTIC_PREFLIGHT_FREE_BYTES:
        raise RuntimeDiagnosticStopped("disk_preflight_below_15_gib")
    selected_nonce = nonce or secrets.token_hex(32)
    if len(selected_nonce) != 64 or any(c not in "0123456789abcdef" for c in selected_nonce):
        raise ContractError("Runtime diagnostic nonce must be 32-byte lowercase hex")
    prepared_ms = int(wall_clock() * 1000)
    record_payload = {
        "schema_version": "cernora.reference.runtime-diagnostic-execution/v1",
        "plan_id": plan.plan_id,
        "authorization_request_id": request.request_id,
        "custody_path_sha256": custody_sha256,
        "nonce": selected_nonce,
        "prepared_unix_milliseconds": prepared_ms,
    }
    record_payload["execution_id"] = canonical_content_id(
        {
            "authorization_request_id": request.request_id,
            "custody_path_sha256": custody_sha256,
            "nonce": selected_nonce,
            "plan_id": plan.plan_id,
        },
        excluded=frozenset(),
    )
    record = RuntimeDiagnosticExecutionRecord.model_validate(record_payload)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    published = False
    try:
        (staging / ".writer.lock").write_bytes(b"")
        (staging / "plan.json").write_bytes(plan.canonical_bytes())
        (staging / "request.json").write_bytes(request.canonical_bytes())
        (staging / "record.json").write_bytes(record.canonical_bytes())
        for path in staging.iterdir():
            path.chmod(0o600)
        atomic_publish_directory(staging, destination)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)
    return inspect_runtime_diagnostic_pilot(destination)


def inspect_runtime_diagnostic_pilot(root: Path) -> RuntimeDiagnosticState:
    files = closed_regular_tree(root)
    allowed = {
        ".writer.lock",
        "claim.json",
        "diagnostic.json",
        "incident.json",
        "outcome.json",
        "plan.json",
        "record.json",
        "request.json",
        "attempt/attempt.json",
        "attempt/manifest.json",
        "attempt/terminal.json",
    }
    if not set(files).issubset(allowed) or not {
        ".writer.lock",
        "plan.json",
        "record.json",
        "request.json",
    }.issubset(files):
        raise ContractError("Runtime diagnostic custody has unknown or missing files")
    expected_root_entries = {name for name in files if "/" not in name} | (
        {"attempt"} if any(name.startswith("attempt/") for name in files) else set()
    )
    if {item.name for item in root.iterdir()} != expected_root_entries:
        raise ContractError("Runtime diagnostic custody has unknown or missing entries")
    plan = RuntimeDiagnosticPilotPlan.from_bytes(read_regular_file_bytes(files["plan.json"]))
    request = RuntimeDiagnosticAuthorizationRequest.from_bytes(
        read_regular_file_bytes(files["request.json"])
    )
    record = _model(files["record.json"], RuntimeDiagnosticExecutionRecord)
    assert isinstance(record, RuntimeDiagnosticExecutionRecord)
    claim = _model(files["claim.json"], RuntimeDiagnosticClaim) if "claim.json" in files else None
    incident = (
        _model(files["incident.json"], RuntimeDiagnosticIncident)
        if "incident.json" in files
        else None
    )
    diagnostic = (
        _model(files["diagnostic.json"], RuntimeDiagnosticReceipt)
        if "diagnostic.json" in files
        else None
    )
    assert diagnostic is None or isinstance(diagnostic, RuntimeDiagnosticReceipt)
    outcome = (
        _model(files["outcome.json"], RuntimeDiagnosticOutcome) if "outcome.json" in files else None
    )
    assert claim is None or isinstance(claim, RuntimeDiagnosticClaim)
    assert incident is None or isinstance(incident, RuntimeDiagnosticIncident)
    assert outcome is None or isinstance(outcome, RuntimeDiagnosticOutcome)
    artifact_files = {name for name in files if name.startswith("attempt/")}
    if artifact_files and artifact_files != {
        "attempt/attempt.json",
        "attempt/manifest.json",
        "attempt/terminal.json",
    }:
        raise ContractError("Runtime diagnostic Attempt artifact is incomplete")
    artifact = verify_controlled_attempt_artifact(root / "attempt") if artifact_files else None
    expected_trial_id = canonical_content_id(
        {"execution_id": record.execution_id, "trial_slot_id": plan.trial_slot().trial_slot_id},
        excluded=frozenset(),
    )
    if (
        request.plan_id != plan.plan_id
        or request.case_authority_sha256 != plan.task.authority_sha256
        or record.plan_id != plan.plan_id
        or record.authorization_request_id != request.request_id
        or record.custody_path_sha256 != _custody_path_sha256(root, must_exist=True)
    ):
        raise ContractError("Runtime diagnostic custody authorities contradict")
    if claim is not None and (
        claim.execution_id != record.execution_id
        or claim.trial_id != expected_trial_id
        or claim.observed_unix_milliseconds < record.prepared_unix_milliseconds
    ):
        raise ContractError("Runtime diagnostic claim contradicts custody")
    if incident is not None and (
        claim is None
        or incident.execution_id != record.execution_id
        or incident.claim_id != claim.claim_id
        or incident.observed_unix_milliseconds < claim.observed_unix_milliseconds
        or artifact is not None
        or outcome is not None
    ):
        raise ContractError("Runtime diagnostic incident contradicts custody")
    if artifact is not None:
        if claim is None or artifact.attempt.trial_id != expected_trial_id:
            raise ContractError("Runtime diagnostic artifact contradicts claim")
        artifact.attempt.verify_authority(plan.specification)
        if artifact.attempt.ordinal != 1 or artifact.attempt.predecessor_attempt_id is not None:
            raise ContractError("Runtime diagnostic artifact exceeds one Attempt")
    if outcome is not None and (
        claim is None
        or artifact is None
        or outcome.execution_id != record.execution_id
        or outcome.plan_id != plan.plan_id
        or outcome.claim_id != claim.claim_id
        or outcome.attempt_id != artifact.attempt.attempt_id
        or outcome.attempt_artifact_id != artifact.manifest.artifact_id
        or outcome.classification != _classification(artifact.attempt)
        or outcome.diagnostic_code != (None if diagnostic is None else diagnostic.diagnostic_code)
        or outcome.completed_unix_milliseconds < claim.observed_unix_milliseconds
    ):
        raise ContractError("Runtime diagnostic outcome contradicts terminal evidence")
    if claim is None and (
        artifact is not None
        or incident is not None
        or outcome is not None
        or diagnostic is not None
    ):
        raise ContractError("Runtime diagnostic custody omits its Attempt claim")
    if diagnostic is not None and (
        claim is None
        or diagnostic.execution_id != record.execution_id
        or diagnostic.claim_id != claim.claim_id
    ):
        raise ContractError("Runtime diagnostic receipt contradicts custody")
    return RuntimeDiagnosticState(
        plan, request, record, claim, artifact, outcome, incident, diagnostic
    )


@contextmanager
def _writer_lock(root: Path) -> Iterator[None]:
    lock = root / ".writer.lock"
    with lock.open("r+b", buffering=0) as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContractError("Runtime diagnostic pilot already has an active writer") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _classification(attempt: ControlledAttempt) -> str:
    if attempt.lifecycle is None:
        return "evaluated"
    if attempt.lifecycle.category == "timed_out":
        return "timed-out"
    return "inconclusive"


def _publish_outcome(
    root: Path,
    state: RuntimeDiagnosticState,
    *,
    completed_ms: int,
) -> RuntimeDiagnosticOutcome:
    assert state.claim is not None and state.artifact is not None
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.runtime-diagnostic-outcome/v1",
        "execution_id": state.record.execution_id,
        "plan_id": state.plan.plan_id,
        "claim_id": state.claim.claim_id,
        "attempt_id": state.artifact.attempt.attempt_id,
        "attempt_artifact_id": state.artifact.manifest.artifact_id,
        "classification": _classification(state.artifact.attempt),
        "diagnostic_code": (None if state.diagnostic is None else state.diagnostic.diagnostic_code),
        "terminal_evidence": "controlled-attempt-terminal",
        "claim_authority": "diagnostic-only",
        "no_retry": True,
        "completed_unix_milliseconds": completed_ms,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    payload["outcome_id"] = canonical_content_id(payload, excluded=frozenset())
    outcome = RuntimeDiagnosticOutcome.model_validate(payload)
    _write_exclusive(root / "outcome.json", outcome.canonical_bytes())
    return outcome


def _publish_diagnostic_receipt(
    root: Path,
    state: RuntimeDiagnosticState,
    *,
    diagnostic_code: str,
) -> None:
    assert state.claim is not None
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.runtime-diagnostic-receipt/v1",
        "execution_id": state.record.execution_id,
        "claim_id": state.claim.claim_id,
        "diagnostic_code": diagnostic_code,
    }
    payload["receipt_id"] = canonical_content_id(payload, excluded=frozenset())
    receipt = RuntimeDiagnosticReceipt.model_validate(payload)
    _write_exclusive(root / "diagnostic.json", receipt.canonical_bytes())


def _publish_incident(
    root: Path,
    state: RuntimeDiagnosticState,
    *,
    phase: Literal["executor", "attempt-validation", "artifact-publication"],
    observed_ms: int,
) -> None:
    assert state.claim is not None
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.runtime-diagnostic-incident/v1",
        "execution_id": state.record.execution_id,
        "claim_id": state.claim.claim_id,
        "phase": phase,
        "category": "ambiguous-one-shot-attempt",
        "observed_unix_milliseconds": observed_ms,
    }
    payload["incident_id"] = canonical_content_id(payload, excluded=frozenset())
    incident = RuntimeDiagnosticIncident.model_validate(payload)
    _write_exclusive(root / "incident.json", incident.canonical_bytes())


def _step_runtime_diagnostic_pilot(
    root: Path,
    executor: ControlledAttemptExecutor,
    *,
    accepted_plan_id: str,
    accepted_request_id: str,
    wall_clock: WallClock = time.time,
    clock: Clock = time.monotonic,
    disk_free: DiskProbe = lambda path: shutil.disk_usage(path).free,
    hard_deadline_monotonic: float | None = None,
) -> RuntimeDiagnosticState:
    """Private executor seam used by the closed public entry point and offline tests."""

    with _writer_lock(root):
        deadline = (
            clock() + DIAGNOSTIC_MAX_WALL_SECONDS
            if hard_deadline_monotonic is None
            else hard_deadline_monotonic
        )
        if clock() >= deadline:
            raise RuntimeDiagnosticStopped("runtime_diagnostic_wall_deadline_elapsed")
        state = inspect_runtime_diagnostic_pilot(root)
        if (
            accepted_plan_id != state.plan.plan_id
            or accepted_request_id != state.request.request_id
        ):
            raise ContractError("Runtime diagnostic acceptance does not equal Plan and request")
        if state.outcome is not None:
            return state
        if state.incident is not None:
            raise AmbiguousRuntimeDiagnosticAttempt(
                "Runtime diagnostic Attempt is ambiguous and cannot be retried"
            )
        if state.artifact is not None:
            if clock() >= deadline:
                raise RuntimeDiagnosticStopped("runtime_diagnostic_wall_deadline_elapsed")
            _publish_outcome(
                root,
                state,
                completed_ms=max(
                    state.claim.observed_unix_milliseconds if state.claim else 0,
                    int(wall_clock() * 1000),
                ),
            )
            return inspect_runtime_diagnostic_pilot(root)
        if state.claim is not None:
            raise AmbiguousRuntimeDiagnosticAttempt(
                "Runtime diagnostic Attempt is claimed without terminal evidence"
            )
        if not executor.enforces_hard_deadline:
            raise ContractError("Runtime diagnostic executor must enforce the hard deadline")
        if disk_free(root) < DIAGNOSTIC_SAFE_STOP_FREE_BYTES:
            raise RuntimeDiagnosticStopped("disk_safe_stop_below_8_gib")
        if clock() >= deadline:
            raise RuntimeDiagnosticStopped("runtime_diagnostic_wall_deadline_elapsed")
        observed_ms = int(wall_clock() * 1000)
        slot = state.plan.trial_slot()
        trial_id = canonical_content_id(
            {"execution_id": state.record.execution_id, "trial_slot_id": slot.trial_slot_id},
            excluded=frozenset(),
        )
        claim_payload: dict[str, object] = {
            "schema_version": "cernora.reference.runtime-diagnostic-claim/v1",
            "execution_id": state.record.execution_id,
            "trial_id": trial_id,
            "ordinal": 1,
            "predecessor_attempt_id": None,
            "observed_unix_milliseconds": observed_ms,
        }
        claim_payload["claim_id"] = canonical_content_id(claim_payload, excluded=frozenset())
        claim = RuntimeDiagnosticClaim.model_validate(claim_payload)
        _write_exclusive(root / "claim.json", claim.canonical_bytes())
        state = inspect_runtime_diagnostic_pilot(root)
        attempt_request = ControlledAttemptRequest(
            trial_id=trial_id,
            slot=slot,
            specification=state.plan.specification,
            ordinal=1,
            predecessor_attempt_id=None,
            global_deadline_monotonic=deadline,
        )
        try:
            attempt = executor(attempt_request)
        except BaseException:
            _publish_incident(
                root,
                state,
                phase="executor",
                observed_ms=max(observed_ms, int(wall_clock() * 1000)),
            )
            raise AmbiguousRuntimeDiagnosticAttempt(
                "Runtime diagnostic Attempt failed without adoptable terminal evidence"
            ) from None
        diagnostic_code = getattr(executor, "diagnostic_code", None)
        if diagnostic_code is not None:
            _publish_diagnostic_receipt(
                root,
                state,
                diagnostic_code=diagnostic_code,
            )
            state = inspect_runtime_diagnostic_pilot(root)
        try:
            if clock() >= deadline:
                raise RuntimeDiagnosticStopped("attempt_returned_after_hard_deadline")
            if (
                attempt.trial_id != trial_id
                or attempt.ordinal != 1
                or attempt.predecessor_attempt_id is not None
            ):
                raise ContractError("Runtime diagnostic executor returned another Attempt")
            attempt.verify_authority(state.plan.specification)
        except BaseException:
            _publish_incident(
                root,
                state,
                phase="attempt-validation",
                observed_ms=max(observed_ms, int(wall_clock() * 1000)),
            )
            raise
        try:
            if clock() >= deadline:
                raise RuntimeDiagnosticStopped("runtime_diagnostic_wall_deadline_elapsed")
            publish_controlled_attempt_artifact(
                root / "attempt",
                attempt=attempt,
                specification=state.plan.specification,
            )
        except BaseException:
            if not (root / "attempt").exists():
                _publish_incident(
                    root,
                    state,
                    phase="artifact-publication",
                    observed_ms=max(observed_ms, int(wall_clock() * 1000)),
                )
            raise
        state = inspect_runtime_diagnostic_pilot(root)
        if clock() >= deadline:
            raise RuntimeDiagnosticStopped("runtime_diagnostic_wall_deadline_elapsed")
        _publish_outcome(
            root,
            state,
            completed_ms=max(observed_ms, int(wall_clock() * 1000)),
        )
        state = inspect_runtime_diagnostic_pilot(root)
        if clock() >= deadline:
            raise RuntimeDiagnosticStopped("runtime_diagnostic_wall_deadline_elapsed")
        return state


def step_runtime_diagnostic_pilot(
    root: Path,
    *,
    repository_root: Path,
    accepted_plan_id: str,
    accepted_request_id: str,
    cernora_wheel: Path | None = None,
    companion_wheel: Path | None = None,
    auth_file: Path | None = None,
    proxy_environment: Mapping[str, str] | None = None,
) -> RuntimeDiagnosticState:
    """Run the exact pi/Harbor diagnostic path or adopt its terminal artifact."""

    started = time.monotonic()
    deadline = started + DIAGNOSTIC_MAX_WALL_SECONDS
    state = inspect_runtime_diagnostic_pilot(root)
    repository = repository_root.resolve(strict=True)
    expected_custody = runtime_diagnostic_custody_path(repository, state.plan.plan_id)
    if (
        Path(os.path.abspath(root)) != expected_custody
        or root.resolve(strict=True) != expected_custody
    ):
        raise ContractError("Runtime diagnostic step uses another custody path")
    if time.monotonic() >= deadline:
        raise RuntimeDiagnosticStopped("runtime_diagnostic_wall_deadline_elapsed")
    if state.status != "prepared":
        return _step_runtime_diagnostic_pilot(
            root,
            _RecoveryOnlyExecutor(),
            accepted_plan_id=accepted_plan_id,
            accepted_request_id=accepted_request_id,
            wall_clock=time.time,
            clock=time.monotonic,
            disk_free=lambda path: shutil.disk_usage(path).free,
            hard_deadline_monotonic=deadline,
        )
    if (
        cernora_wheel is None
        or companion_wheel is None
        or auth_file is None
        or proxy_environment is None
    ):
        raise ContractError("Runtime diagnostic prepared step requires exact Runtime inputs")
    verify_runtime_diagnostic_runtime(
        state.plan,
        repository_root=repository,
        cernora_wheel=cernora_wheel,
        companion_wheel=companion_wheel,
    )
    if time.monotonic() >= deadline:
        raise RuntimeDiagnosticStopped("runtime_diagnostic_wall_deadline_elapsed")

    from cernora_reference_workflow.controlled_live_attempt import (
        ControlledHarborAttemptExecutor,
    )

    with tempfile.TemporaryDirectory(prefix="cernora-runtime-diagnostic-evaluation-") as temporary:
        executor = ControlledHarborAttemptExecutor(
            repository_root=repository,
            tasks=(state.plan.task,),
            evaluation_root=Path(temporary),
            auth_file=auth_file,
            proxy_environment=proxy_environment,
            close_unusable_runtime_evidence=True,
            attempt_envelope_grace_seconds=(
                state.plan.attempt_envelope_timeout_seconds
                - state.plan.specification.limits.timeout_seconds
            ),
        )
        return _step_runtime_diagnostic_pilot(
            root,
            executor,
            accepted_plan_id=accepted_plan_id,
            accepted_request_id=accepted_request_id,
            wall_clock=time.time,
            clock=time.monotonic,
            disk_free=lambda path: shutil.disk_usage(path).free,
            hard_deadline_monotonic=deadline,
        )


__all__ = [
    "AmbiguousRuntimeDiagnosticAttempt",
    "RuntimeDiagnosticAuthorizationRequest",
    "RuntimeDiagnosticOutcome",
    "RuntimeDiagnosticPilotPlan",
    "RuntimeDiagnosticState",
    "RuntimeDiagnosticStopped",
    "build_runtime_diagnostic_authorization_request",
    "build_runtime_diagnostic_pilot_plan",
    "create_runtime_diagnostic_proposal",
    "inspect_runtime_diagnostic_pilot",
    "inspect_runtime_diagnostic_proposal",
    "prepare_runtime_diagnostic_pilot",
    "runtime_diagnostic_custody_path",
    "step_runtime_diagnostic_pilot",
    "verify_runtime_diagnostic_runtime",
]
