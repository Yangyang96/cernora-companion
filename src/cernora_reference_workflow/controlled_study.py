"""Strict contracts for the durable Priority 4 Controlled Study seam."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, StrictStr, TypeAdapter, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
    read_regular_file_bytes,
    sha256_bytes,
    sha256_file,
)
from cernora_reference_workflow.experiment_spec import Digest, StrictContract
from cernora_reference_workflow.publication import atomic_publish_directory

ImplementationKind = Literal["wheel", "source-tree", "container-image", "policy-bundle"]
ImplementationName = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
NonEmpty = Annotated[StrictStr, Field(min_length=1)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
StudyKind = Literal["contract-proof", "confirmatory-effect"]
StudySplit = Literal["development", "regression", "held-out"]
ConfigurationRole = Literal["baseline", "candidate"]
TreatmentAxis = Literal[
    "prompt-instruction",
    "model",
    "tool-schema",
    "generation-configuration",
    "runtime-version",
]
ConfirmatoryStopReason = Literal[
    "ambiguous-active-attempt",
    "budget-exhausted",
    "integrity-failure",
    "operator-request",
    "safety-limit",
]
ArtifactKind = Literal["diagnostic-pack", "evidence-pack"]
ClaimAuthority = Literal["diagnostic-only", "descriptive-only", "confirmatory"]
ControlledStudyErrorCode = Literal[
    "invalid-intent",
    "authority-mismatch",
    "stale-acceptance",
    "ambiguous-active-attempt",
    "adapter-violation",
    "corrupt-ledger",
    "incomplete-pack",
    "destination-conflict",
]
ControlledStudyPhase = Literal["prepare", "advance", "rebuild"]

CONFIRMATORY_STOP_REASONS: tuple[ConfirmatoryStopReason, ...] = (
    "ambiguous-active-attempt",
    "budget-exhausted",
    "integrity-failure",
    "operator-request",
    "safety-limit",
)


class ControlledStudyError(ContractError):
    """Stable structural failure at the public Controlled Study seam."""

    def __init__(
        self,
        code: ControlledStudyErrorCode,
        *,
        phase: ControlledStudyPhase,
        artifact_id: Digest | None = None,
    ) -> None:
        self.code = code
        self.phase = phase
        self.artifact_id = artifact_id
        super().__init__(f"{phase}:{code}")


class ImplementationArtifact(StrictContract):
    """One exact implementation artifact, not merely a display version."""

    name: ImplementationName
    version: Annotated[StrictStr, Field(min_length=1)]
    kind: ImplementationKind
    sha256: Digest


class ImplementationLock(StrictContract):
    """Closed implementation authority required before external work."""

    schema_version: Literal["cernora.reference.implementation-lock/v1"]
    lock_id: Digest
    companion: ImplementationArtifact
    cernora: ImplementationArtifact
    runtime_adapter: ImplementationArtifact
    harness: ImplementationArtifact
    analysis_policy: ImplementationArtifact

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"lock_id"})
        )
        if self.lock_id != expected:
            raise ValueError("ImplementationLock identity does not match canonical content")
        return self


def materialize_implementation_lock(
    payload_without_identity: Mapping[str, object],
) -> ImplementationLock:
    """Create one canonical lock from exact implementation artifacts."""

    if "lock_id" in payload_without_identity:
        raise ContractError("ImplementationLock materialization input must omit lock_id")
    payload = dict(payload_without_identity)
    payload["lock_id"] = canonical_content_id(payload, excluded=frozenset())
    return ImplementationLock.model_validate(payload)


class BaselineAuthority(StrictContract):
    configuration_id: Literal["baseline"]
    authority_sha256: Digest


class CandidatePatch(StrictContract):
    """One declared Treatment applied over the frozen Baseline authority."""

    configuration_id: Literal["candidate"]
    baseline_authority_sha256: Digest
    authority_sha256: Digest
    treatment_axis: TreatmentAxis
    treatment_sha256: Digest

    @model_validator(mode="after")
    def substantive_change(self) -> Self:
        if self.authority_sha256 == self.baseline_authority_sha256:
            raise ValueError("CandidatePatch must change the Baseline authority")
        return self


class CandidateHypothesis(StrictContract):
    """Predeclared causal account for one Candidate intervention."""

    observed_failure_code: Identifier
    mechanism: NonEmpty
    intervention_scope: NonEmpty
    expected_observation: NonEmpty
    falsifier: NonEmpty


class StudyCaseAuthority(StrictContract):
    case_id: Identifier
    split: StudySplit
    authority_sha256: Digest


class StudyIntent(StrictContract):
    """Caller-owned semantic choices before schedule and derived counts exist."""

    schema_version: Literal["cernora.reference.study-intent/v1"]
    intent_id: Digest
    study_kind: StudyKind
    baseline: BaselineAuthority
    candidate: CandidatePatch
    hypothesis: CandidateHypothesis
    cases: tuple[StudyCaseAuthority, ...] = Field(min_length=3)
    repetitions: PositiveInt
    max_attempt_count: PositiveInt
    max_wall_seconds: PositiveInt
    heldout_manifest_sha256: Digest
    analysis_policy_sha256: Digest
    implementation_lock: ImplementationLock

    @field_validator("cases", mode="before")
    @classmethod
    def tuple_cases(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def closed_semantics_and_identity(self) -> Self:
        case_ids = tuple(item.case_id for item in self.cases)
        if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("StudyIntent Cases must be sorted and unique")
        if {item.split for item in self.cases} != {"development", "regression", "held-out"}:
            raise ValueError("StudyIntent requires development, regression, and held-out Cases")
        if self.candidate.baseline_authority_sha256 != self.baseline.authority_sha256:
            raise ValueError("CandidatePatch does not derive from the frozen Baseline")
        planned = len(self.cases) * 2 * self.repetitions
        if self.max_attempt_count < planned:
            raise ValueError("Attempt budget cannot omit a planned Trial")
        if self.analysis_policy_sha256 != self.implementation_lock.analysis_policy.sha256:
            raise ValueError("analysis policy does not match ImplementationLock")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"intent_id"})
        )
        if self.intent_id != expected:
            raise ValueError("StudyIntent identity does not match canonical content")
        return self


def materialize_study_intent(payload_without_identity: Mapping[str, object]) -> StudyIntent:
    """Freeze caller-owned study semantics without caller-authored derived identity."""

    if "intent_id" in payload_without_identity:
        raise ContractError("StudyIntent materialization input must omit intent_id")
    payload = dict(payload_without_identity)
    payload["intent_id"] = canonical_content_id(payload, excluded=frozenset())
    return StudyIntent.model_validate(payload)


class StudyPairBlock(StrictContract):
    """One adjacent paired AB/BA block in the frozen execution schedule."""

    block_index: PositiveInt
    case_id: Identifier
    split: StudySplit
    repetition: PositiveInt
    configuration_order: tuple[ConfigurationRole, ConfigurationRole]

    @field_validator("configuration_order", mode="before")
    @classmethod
    def tuple_order(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_pair(self) -> Self:
        if set(self.configuration_order) != {"baseline", "candidate"}:
            raise ValueError("Study Pair block must contain Baseline and Candidate exactly once")
        return self


class StudyClaimPolicy(StrictContract):
    development: Literal["descriptive"]
    regression: Literal["guardrail"]
    heldout: Literal["confirmatory-primary"]
    effect_conclusion: Literal["descriptive-only", "confirmatory"]


class StudyProtocol(StrictContract):
    """Fully derived protocol frozen before any external execution."""

    schema_version: Literal["cernora.reference.study-protocol/v1"]
    protocol_id: Digest
    source_intent_id: Digest
    implementation_lock_id: Digest
    schedule_method: Literal["case-repetition-blocked-ab-ba/v1"]
    blocks: tuple[StudyPairBlock, ...] = Field(min_length=3)
    planned_trial_count: PositiveInt
    claims: StudyClaimPolicy
    confirmatory_quality_stop: Literal[False]
    confirmatory_stop_reasons: tuple[ConfirmatoryStopReason, ...]

    @field_validator("blocks", "confirmatory_stop_reasons", mode="before")
    @classmethod
    def tuple_collections(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_schedule_and_identity(self) -> Self:
        if tuple(item.block_index for item in self.blocks) != tuple(range(1, len(self.blocks) + 1)):
            raise ValueError("StudyProtocol block indexes must be contiguous")
        coordinates = tuple((item.case_id, item.repetition) for item in self.blocks)
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("StudyProtocol contains a duplicate Case/Repetition block")
        if self.planned_trial_count != len(self.blocks) * 2:
            raise ValueError("planned Trial count does not match paired schedule")
        if self.confirmatory_stop_reasons != CONFIRMATORY_STOP_REASONS:
            raise ValueError("confirmatory stop reasons are not the frozen safety-only policy")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"protocol_id"})
        )
        if self.protocol_id != expected:
            raise ValueError("StudyProtocol identity does not match canonical content")
        return self


def compile_study_protocol(intent: StudyIntent) -> StudyProtocol:
    """Derive claims, counts, and an adjacent counterbalanced schedule from one Intent."""

    blocks: list[dict[str, object]] = []
    for repetition in range(1, intent.repetitions + 1):
        for case_index, case in enumerate(intent.cases):
            baseline_first = (case_index + repetition) % 2 == 1
            blocks.append(
                {
                    "block_index": len(blocks) + 1,
                    "case_id": case.case_id,
                    "split": case.split,
                    "repetition": repetition,
                    "configuration_order": (
                        ("baseline", "candidate") if baseline_first else ("candidate", "baseline")
                    ),
                }
            )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.study-protocol/v1",
        "source_intent_id": intent.intent_id,
        "implementation_lock_id": intent.implementation_lock.lock_id,
        "schedule_method": "case-repetition-blocked-ab-ba/v1",
        "blocks": blocks,
        "planned_trial_count": len(blocks) * 2,
        "claims": {
            "development": "descriptive",
            "regression": "guardrail",
            "heldout": "confirmatory-primary",
            "effect_conclusion": (
                "descriptive-only" if intent.study_kind == "contract-proof" else "confirmatory"
            ),
        },
        "confirmatory_quality_stop": False,
        "confirmatory_stop_reasons": CONFIRMATORY_STOP_REASONS,
    }
    payload["protocol_id"] = canonical_content_id(payload, excluded=frozenset())
    return StudyProtocol.model_validate(payload)


class StudyRecord(StrictContract):
    """Root authority for one durably prepared Controlled Study."""

    schema_version: Literal["cernora.reference.controlled-study/v1"]
    study_id: Digest
    nonce: Digest
    intent_id: Digest
    protocol_id: Digest
    implementation_lock_id: Digest
    intent_sha256: Digest
    protocol_sha256: Digest

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            {"nonce": self.nonce, "protocol_id": self.protocol_id},
            excluded=frozenset(),
        )
        if self.study_id != expected:
            raise ValueError("Controlled Study identity does not bind nonce and Protocol")
        return self


class PreparedLedgerEntry(StrictContract):
    """First hash-chained fact in every Controlled Study ledger."""

    schema_version: Literal["cernora.reference.study-ledger-entry/v1"]
    entry_id: Digest
    study_id: Digest
    sequence: Literal[1]
    previous_entry_sha256: None
    operation_id: Digest
    event: Literal["prepared"]
    protocol_id: Digest

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("Study ledger entry identity does not match canonical content")
        return self


class StudyArtifactManifest(StrictContract):
    """Top-level closure binding one terminal state to its locally complete evidence."""

    schema_version: Literal["cernora.reference.study-artifact-manifest/v1"]
    artifact_id: Digest
    kind: ArtifactKind
    protocol_id: Digest
    implementation_lock_id: Digest
    ledger_root_sha256: Digest
    terminal_status: Literal["paused", "completed", "terminated"]
    claim_authority: ClaimAuthority
    batch_package_sha256: Digest | None
    comparison_package_sha256: Digest | None
    report_sha256: Digest

    @model_validator(mode="after")
    def coherent_authority_and_identity(self) -> Self:
        core_packages = (self.batch_package_sha256, self.comparison_package_sha256)
        if self.terminal_status == "completed":
            if (
                self.kind != "evidence-pack"
                or self.claim_authority == "diagnostic-only"
                or any(item is None for item in core_packages)
            ):
                raise ValueError("completed Study requires one authoritative Evidence Pack")
        elif (
            self.kind != "diagnostic-pack"
            or self.claim_authority != "diagnostic-only"
            or any(item is not None for item in core_packages)
        ):
            raise ValueError("incomplete Study may publish only diagnostic authority")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"artifact_id"})
        )
        if self.artifact_id != expected:
            raise ValueError("Study artifact identity does not match canonical content")
        return self


def materialize_study_artifact_manifest(
    payload_without_identity: Mapping[str, object],
) -> StudyArtifactManifest:
    if "artifact_id" in payload_without_identity:
        raise ContractError("Study artifact materialization input must omit artifact_id")
    payload = dict(payload_without_identity)
    payload["artifact_id"] = canonical_content_id(payload, excluded=frozenset())
    return StudyArtifactManifest.model_validate(payload)


class StudyArtifactReference(StrictContract):
    kind: ArtifactKind
    artifact_id: Digest


class ExecutionOutcomeBase(StrictContract):
    schema_version: Literal["cernora.reference.execution-outcome/v1"]
    state_id: Digest
    study_id: Digest
    protocol_id: Digest
    ledger_root_sha256: Digest

    @model_validator(mode="after")
    def canonical_state_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"state_id"})
        )
        if self.state_id != expected:
            raise ValueError("ExecutionOutcome identity does not match canonical content")
        return self


class PreparedOutcome(ExecutionOutcomeBase):
    status: Literal["prepared"]


class AwaitingRevealOutcome(ExecutionOutcomeBase):
    status: Literal["awaiting-reveal"]


class AwaitingAcceptanceOutcome(ExecutionOutcomeBase):
    status: Literal["awaiting-acceptance"]
    acceptance_id: Digest


class RunningOutcome(ExecutionOutcomeBase):
    status: Literal["running"]
    execution_id: Digest


class PausedOutcome(ExecutionOutcomeBase):
    status: Literal["paused"]
    execution_id: Digest
    reason: Literal["operator-request", "safety-limit", "ambiguous-active-attempt"]
    artifact: StudyArtifactReference
    resumable: Literal[True]

    @model_validator(mode="after")
    def diagnostic_only(self) -> Self:
        if self.artifact.kind != "diagnostic-pack":
            raise ValueError("paused Study requires a diagnostic pack")
        return self


class CompletedOutcome(ExecutionOutcomeBase):
    status: Literal["completed"]
    execution_id: Digest
    artifact: StudyArtifactReference
    claim_authority: Literal["descriptive-only", "confirmatory"]

    @model_validator(mode="after")
    def authoritative_pack(self) -> Self:
        if self.artifact.kind != "evidence-pack":
            raise ValueError("completed Study requires an Evidence Pack")
        return self


class TerminatedOutcome(ExecutionOutcomeBase):
    status: Literal["terminated"]
    execution_id: Digest | None
    reason: Literal["budget-exhausted", "integrity-failure"]
    artifact: StudyArtifactReference
    resumable: Literal[False]

    @model_validator(mode="after")
    def diagnostic_only(self) -> Self:
        if self.artifact.kind != "diagnostic-pack":
            raise ValueError("terminated Study requires a diagnostic pack")
        return self


ExecutionOutcome = Annotated[
    PreparedOutcome
    | AwaitingRevealOutcome
    | AwaitingAcceptanceOutcome
    | RunningOutcome
    | PausedOutcome
    | CompletedOutcome
    | TerminatedOutcome,
    Field(discriminator="status"),
]
_EXECUTION_OUTCOME_ADAPTER: TypeAdapter[ExecutionOutcome] = TypeAdapter(ExecutionOutcome)


def materialize_execution_outcome(
    payload_without_identity: Mapping[str, object],
) -> ExecutionOutcome:
    if "state_id" in payload_without_identity:
        raise ContractError("ExecutionOutcome materialization input must omit state_id")
    payload = dict(payload_without_identity)
    payload["state_id"] = canonical_content_id(payload, excluded=frozenset())
    return _EXECUTION_OUTCOME_ADAPTER.validate_python(payload)


def _canonical_contract_bytes(value: StrictContract) -> bytes:
    return canonical_json_bytes(value.model_dump(mode="json"))


def _load_canonical_contract[ModelT: StrictContract](path: Path, model: type[ModelT]) -> ModelT:
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise ContractError(f"{path.name} must contain a JSON object")
    parsed = model.model_validate(payload)
    if read_regular_file_bytes(path) != _canonical_contract_bytes(parsed):
        raise ContractError(f"{path.name} is not canonical JSON")
    return parsed


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_durable_file(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _temporary_roots() -> tuple[Path, ...]:
    candidates = (Path(tempfile.gettempdir()), Path("/tmp"), Path("/private/tmp"))
    return tuple(dict.fromkeys(item.resolve() for item in candidates))


def _require_durable_destination(destination: Path) -> Path:
    resolved = destination.resolve()
    if any(resolved == root or resolved.is_relative_to(root) for root in _temporary_roots()):
        raise ControlledStudyError("invalid-intent", phase="prepare")
    try:
        parent_metadata = resolved.parent.lstat()
    except OSError as exc:
        raise ControlledStudyError("invalid-intent", phase="prepare") from exc
    if not stat.S_ISDIR(parent_metadata.st_mode) or resolved.parent.is_symlink():
        raise ControlledStudyError("invalid-intent", phase="prepare")
    return resolved


def _load_prepared_study(root: Path) -> tuple[StudyIntent, PreparedOutcome]:
    expected_files = {
        ".writer.lock",
        "intent.json",
        "ledger/00000001.json",
        "protocol.json",
        "study.json",
    }
    files = closed_regular_tree(root)
    if set(files) != expected_files:
        raise ContractError("Controlled Study root is not a closed prepared tree")
    if read_regular_file_bytes(root / ".writer.lock", maximum=0) != b"":
        raise ContractError("Controlled Study writer lock is malformed")
    intent = _load_canonical_contract(root / "intent.json", StudyIntent)
    protocol = _load_canonical_contract(root / "protocol.json", StudyProtocol)
    record = _load_canonical_contract(root / "study.json", StudyRecord)
    entry = _load_canonical_contract(root / "ledger" / "00000001.json", PreparedLedgerEntry)
    if protocol != compile_study_protocol(intent):
        raise ContractError("stored Study Protocol is not derived from its Intent")
    if (
        record.intent_id != intent.intent_id
        or record.protocol_id != protocol.protocol_id
        or record.implementation_lock_id != protocol.implementation_lock_id
        or record.intent_sha256 != sha256_file(root / "intent.json")
        or record.protocol_sha256 != sha256_file(root / "protocol.json")
        or entry.study_id != record.study_id
        or entry.protocol_id != protocol.protocol_id
        or entry.operation_id != record.study_id
    ):
        raise ContractError("prepared Controlled Study authority mismatch")
    outcome = materialize_execution_outcome(
        {
            "schema_version": "cernora.reference.execution-outcome/v1",
            "study_id": record.study_id,
            "protocol_id": protocol.protocol_id,
            "ledger_root_sha256": sha256_file(root / "ledger" / "00000001.json"),
            "status": "prepared",
        }
    )
    if not isinstance(outcome, PreparedOutcome):
        raise ContractError("prepared ledger did not materialize a Prepared outcome")
    return intent, outcome


def prepare(intent: StudyIntent, destination: Path) -> PreparedOutcome:
    """Freeze and durably publish one Controlled Study without external work."""

    root = _require_durable_destination(destination)
    if root.exists() or root.is_symlink():
        try:
            stored_intent, outcome = _load_prepared_study(root)
        except (ContractError, OSError, ValueError) as exc:
            raise ControlledStudyError("corrupt-ledger", phase="prepare") from exc
        if stored_intent.intent_id != intent.intent_id:
            raise ControlledStudyError(
                "destination-conflict", phase="prepare", artifact_id=outcome.study_id
            )
        return outcome

    protocol = compile_study_protocol(intent)
    intent_bytes = _canonical_contract_bytes(intent)
    protocol_bytes = _canonical_contract_bytes(protocol)
    nonce = secrets.token_hex(32)
    study_id = canonical_content_id(
        {"nonce": nonce, "protocol_id": protocol.protocol_id}, excluded=frozenset()
    )
    record = StudyRecord(
        schema_version="cernora.reference.controlled-study/v1",
        study_id=study_id,
        nonce=nonce,
        intent_id=intent.intent_id,
        protocol_id=protocol.protocol_id,
        implementation_lock_id=protocol.implementation_lock_id,
        intent_sha256=sha256_bytes(intent_bytes),
        protocol_sha256=sha256_bytes(protocol_bytes),
    )
    entry_payload: dict[str, object] = {
        "schema_version": "cernora.reference.study-ledger-entry/v1",
        "study_id": study_id,
        "sequence": 1,
        "previous_entry_sha256": None,
        "operation_id": study_id,
        "event": "prepared",
        "protocol_id": protocol.protocol_id,
    }
    entry_payload["entry_id"] = canonical_content_id(entry_payload, excluded=frozenset())
    entry = PreparedLedgerEntry.model_validate(entry_payload)

    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.staging-", dir=root.parent))
    try:
        (staging / "ledger").mkdir()
        _write_durable_file(staging / ".writer.lock", b"")
        _write_durable_file(staging / "intent.json", intent_bytes)
        _write_durable_file(staging / "protocol.json", protocol_bytes)
        _write_durable_file(staging / "study.json", _canonical_contract_bytes(record))
        _write_durable_file(staging / "ledger" / "00000001.json", _canonical_contract_bytes(entry))
        _sync_directory(staging / "ledger")
        _sync_directory(staging)
        atomic_publish_directory(staging, root)
        _sync_directory(root.parent)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    try:
        _, outcome = _load_prepared_study(root)
    except (ContractError, OSError, ValueError) as exc:
        raise ControlledStudyError("corrupt-ledger", phase="prepare") from exc
    return outcome


__all__ = [
    "AwaitingAcceptanceOutcome",
    "AwaitingRevealOutcome",
    "BaselineAuthority",
    "CandidateHypothesis",
    "CandidatePatch",
    "CompletedOutcome",
    "ControlledStudyError",
    "ControlledStudyErrorCode",
    "ControlledStudyPhase",
    "ExecutionOutcome",
    "ImplementationArtifact",
    "ImplementationLock",
    "PausedOutcome",
    "PreparedOutcome",
    "RunningOutcome",
    "StudyArtifactManifest",
    "StudyArtifactReference",
    "StudyCaseAuthority",
    "StudyClaimPolicy",
    "StudyIntent",
    "StudyPairBlock",
    "StudyProtocol",
    "StudyRecord",
    "TerminatedOutcome",
    "compile_study_protocol",
    "materialize_execution_outcome",
    "materialize_implementation_lock",
    "materialize_study_artifact_manifest",
    "materialize_study_intent",
    "prepare",
]
