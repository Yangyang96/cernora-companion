"""Strict contracts for the durable Priority 4 Controlled Study seam."""

from __future__ import annotations

import fcntl
import os
import secrets
import shutil
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, StrictStr, TypeAdapter, field_validator, model_validator

from cernora_reference_workflow.batch_summary import summarize_execution_pack
from cernora_reference_workflow.candidate_development import (
    BaselineAuthority,
    CandidateDevelopmentRecord,
    CandidateHypothesis,
    CandidatePatch,
)
from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
    read_regular_file_bytes,
    sha256_bytes,
    sha256_file,
    validate_relative_path,
)
from cernora_reference_workflow.comparison_input import compare_batch_summary
from cernora_reference_workflow.comparison_plan import ComparisonPlanV1
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.execution import (
    initialize_execution,
    rebuild_execution_pack,
    reload_execution,
    reload_execution_for_reconciliation,
    verify_execution_pack,
)
from cernora_reference_workflow.experiment_spec import Digest, StrictContract
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.runner import (
    AmbiguousActiveAttempt,
    AttemptExecutor,
    StopPredicate,
    advance_repeat,
)

ImplementationKind = Literal["wheel", "source-tree", "container-image", "policy-bundle"]
ImplementationName = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
StudyKind = Literal["contract-proof", "confirmatory-effect"]
StudySplit = Literal["development", "regression", "held-out"]
ConfigurationRole = Literal["baseline", "candidate"]
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
    "concurrent-writer",
    "invalid-transition",
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


class StudyCaseAuthority(StrictContract):
    case_id: Identifier
    split: StudySplit
    authority_sha256: Digest


class HeldoutReveal(StrictContract):
    """Revealed held-out authorities without task or evaluation content."""

    schema_version: Literal["cernora.reference.heldout-reveal/v1"]
    reveal_id: Digest
    commitment_id: Digest
    manifest_sha256: Digest
    cases: tuple[StudyCaseAuthority, ...] = Field(min_length=1)

    @field_validator("cases", mode="before")
    @classmethod
    def tuple_cases(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def closed_heldout_authority(self) -> Self:
        case_ids = tuple(item.case_id for item in self.cases)
        if (
            case_ids != tuple(sorted(case_ids))
            or len(case_ids) != len(set(case_ids))
            or any(item.split != "held-out" for item in self.cases)
        ):
            raise ValueError("held-out reveal Cases must be sorted, unique, and held-out")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"reveal_id"})
        )
        if self.reveal_id != expected:
            raise ValueError("held-out reveal identity does not match canonical content")
        return self


def materialize_heldout_reveal(
    payload_without_identity: Mapping[str, object],
) -> HeldoutReveal:
    if "reveal_id" in payload_without_identity:
        raise ContractError("held-out reveal input must omit reveal_id")
    payload = dict(payload_without_identity)
    payload["reveal_id"] = canonical_content_id(payload, excluded=frozenset())
    return HeldoutReveal.model_validate(payload)


class HeldoutCommitment(StrictContract):
    """Opaque held-out suite authority frozen without task content."""

    schema_version: Literal["cernora.reference.heldout-commitment/v1"]
    commitment_id: Digest
    manifest_sha256: Digest
    case_count: PositiveInt
    case_commitment_root_sha256: Digest
    reveal_policy_sha256: Digest

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"commitment_id"})
        )
        if self.commitment_id != expected:
            raise ValueError("held-out commitment identity does not match canonical content")
        return self


def materialize_heldout_commitment(
    payload_without_identity: Mapping[str, object],
) -> HeldoutCommitment:
    if "commitment_id" in payload_without_identity:
        raise ContractError("held-out commitment input must omit commitment_id")
    payload = dict(payload_without_identity)
    payload["commitment_id"] = canonical_content_id(payload, excluded=frozenset())
    return HeldoutCommitment.model_validate(payload)


class StudyAnalysisPolicy(StrictContract):
    """Frozen confirmatory analysis and claim boundary."""

    schema_version: Literal["cernora.reference.study-analysis-policy/v1"]
    policy_id: Digest
    primary_outcome: Literal["paired-reliable-success-rate-delta"]
    bootstrap_resamples: Literal[10000]
    confidence_level: Literal["0.95"]
    guardrail_rule: Literal["no-protected-regression"]
    missing_evidence: Literal["inconclusive"]
    claim_source: Literal["held-out-only"]

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"policy_id"})
        )
        if self.policy_id != expected:
            raise ValueError("Study analysis policy identity does not match canonical content")
        return self


def materialize_study_analysis_policy(
    payload_without_identity: Mapping[str, object],
) -> StudyAnalysisPolicy:
    if "policy_id" in payload_without_identity:
        raise ContractError("Study analysis policy input must omit policy_id")
    payload = dict(payload_without_identity)
    payload["policy_id"] = canonical_content_id(payload, excluded=frozenset())
    return StudyAnalysisPolicy.model_validate(payload)


class StudyIntent(StrictContract):
    """Caller-owned semantic choices before schedule and derived counts exist."""

    schema_version: Literal["cernora.reference.study-intent/v1"]
    intent_id: Digest
    study_kind: StudyKind
    candidate_development: CandidateDevelopmentRecord
    cases: tuple[StudyCaseAuthority, ...] = Field(min_length=3)
    repetitions: PositiveInt
    max_attempt_count: PositiveInt
    max_wall_seconds: PositiveInt
    heldout_commitment: HeldoutCommitment
    analysis_policy: StudyAnalysisPolicy
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
        cases_by_id = {item.case_id: item for item in self.cases}
        for observation in self.candidate_development.observations:
            case = cases_by_id.get(observation.case_id)
            if case is None or case.split != observation.split:
                raise ValueError("Candidate Development observation is outside its declared split")
        heldout_count = sum(item.split == "held-out" for item in self.cases)
        if heldout_count != self.heldout_commitment.case_count:
            raise ValueError("held-out commitment count does not match opaque Case authorities")
        planned = len(self.cases) * 2 * self.repetitions
        if self.max_attempt_count < planned:
            raise ValueError("Attempt budget cannot omit a planned Trial")
        policy_sha256 = sha256_bytes(
            canonical_json_bytes(self.analysis_policy.model_dump(mode="json"))
        )
        if policy_sha256 != self.implementation_lock.analysis_policy.sha256:
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
    descriptive_case_ids: tuple[Identifier, ...] = Field(min_length=1)
    guardrail_case_ids: tuple[Identifier, ...] = Field(min_length=1)
    primary_case_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator(
        "descriptive_case_ids", "guardrail_case_ids", "primary_case_ids", mode="before"
    )
    @classmethod
    def tuple_case_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def disjoint_claim_scopes(self) -> Self:
        scopes = (
            self.descriptive_case_ids,
            self.guardrail_case_ids,
            self.primary_case_ids,
        )
        if any(scope != tuple(sorted(scope)) or len(scope) != len(set(scope)) for scope in scopes):
            raise ValueError("claim-scope Case identities must be sorted and unique")
        if len(set().union(*(set(scope) for scope in scopes))) != sum(map(len, scopes)):
            raise ValueError("a Case cannot belong to multiple claim scopes")
        return self


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
            "descriptive_case_ids": [
                case.case_id for case in intent.cases if case.split == "development"
            ],
            "guardrail_case_ids": [
                case.case_id for case in intent.cases if case.split == "regression"
            ],
            "primary_case_ids": [case.case_id for case in intent.cases if case.split == "held-out"],
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


class StudyArtifactReference(StrictContract):
    kind: ArtifactKind
    artifact_id: Digest


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


class AwaitingRevealLedgerEntry(StrictContract):
    schema_version: Literal["cernora.reference.study-ledger-entry/v1"]
    entry_id: Digest
    study_id: Digest
    sequence: PositiveInt
    previous_entry_sha256: Digest
    operation_id: Digest
    event: Literal["awaiting-reveal"]
    protocol_id: Digest

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("Study ledger entry identity does not match canonical content")
        return self


class AwaitingAcceptanceLedgerEntry(StrictContract):
    schema_version: Literal["cernora.reference.study-ledger-entry/v1"]
    entry_id: Digest
    study_id: Digest
    sequence: PositiveInt
    previous_entry_sha256: Digest
    operation_id: Digest
    event: Literal["awaiting-acceptance"]
    protocol_id: Digest
    reveal: HeldoutReveal
    acceptance_id: Digest

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("Study ledger entry identity does not match canonical content")
        return self


class BoundRunningLedgerEntry(StrictContract):
    """Frozen Study authority for the shared Repeat Runner execution plane."""

    schema_version: Literal["cernora.reference.study-ledger-entry/v1"]
    entry_id: Digest
    study_id: Digest
    sequence: PositiveInt
    previous_entry_sha256: Digest
    operation_id: Digest
    event: Literal["execution-started"]
    protocol_id: Digest
    acceptance_id: Digest
    execution_nonce: Digest
    execution_id: Digest
    binding_id: Digest
    ordered_trial_slot_ids: tuple[Digest, ...] = Field(min_length=1)
    run_plan: ControlledRunPlanV2
    comparison_plan: ComparisonPlanV1

    @field_validator("ordered_trial_slot_ids", mode="before")
    @classmethod
    def tuple_trial_slot_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected_execution_id = canonical_content_id(
            {"nonce": self.execution_nonce, "run_plan_id": self.run_plan.run_plan_id},
            excluded=frozenset(),
        )
        if self.execution_id != expected_execution_id:
            raise ValueError("Study Execution identity does not bind the RunPlan and nonce")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("Study ledger entry identity does not match canonical content")
        return self


class ExecutionStepClaimedLedgerEntry(StrictContract):
    """Durable claim for one idempotent Repeat Runner advancement."""

    schema_version: Literal["cernora.reference.study-ledger-entry/v1"]
    entry_id: Digest
    study_id: Digest
    sequence: PositiveInt
    previous_entry_sha256: Digest
    operation_id: Digest
    event: Literal["execution-step-claimed"]
    protocol_id: Digest
    execution_id: Digest
    expected_state_id: Digest
    prior_execution_snapshot_id: Digest

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("Study ledger entry identity does not match canonical content")
        return self


class ExecutionStepAdvancedLedgerEntry(StrictContract):
    """Closed result of one claimed Repeat Runner advancement."""

    schema_version: Literal["cernora.reference.study-ledger-entry/v1"]
    entry_id: Digest
    study_id: Digest
    sequence: PositiveInt
    previous_entry_sha256: Digest
    operation_id: Digest
    event: Literal["execution-step-advanced"]
    protocol_id: Digest
    execution_id: Digest
    claim_entry_id: Digest
    runner_status: Literal["running"]
    execution_snapshot_id: Digest

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("Study ledger entry identity does not match canonical content")
        return self


class PausedLedgerEntry(StrictContract):
    schema_version: Literal["cernora.reference.study-ledger-entry/v1"]
    entry_id: Digest
    study_id: Digest
    sequence: PositiveInt
    previous_entry_sha256: Digest
    operation_id: Digest
    event: Literal["paused"]
    protocol_id: Digest
    execution_id: Digest
    claim_entry_id: Digest
    reason: Literal["operator-request", "safety-limit", "ambiguous-active-attempt"]
    artifact: StudyArtifactReference

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        if self.artifact.kind != "diagnostic-pack":
            raise ValueError("paused Study requires a diagnostic artifact")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("Study ledger entry identity does not match canonical content")
        return self


class TerminatedLedgerEntry(StrictContract):
    schema_version: Literal["cernora.reference.study-ledger-entry/v1"]
    entry_id: Digest
    study_id: Digest
    sequence: PositiveInt
    previous_entry_sha256: Digest
    operation_id: Digest
    event: Literal["terminated"]
    protocol_id: Digest
    execution_id: Digest
    claim_entry_id: Digest
    reason: Literal["budget-exhausted", "integrity-failure"]
    artifact: StudyArtifactReference

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        if self.artifact.kind != "diagnostic-pack":
            raise ValueError("terminated Study requires a diagnostic artifact")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("Study ledger entry identity does not match canonical content")
        return self


class CompletedLedgerEntry(StrictContract):
    schema_version: Literal["cernora.reference.study-ledger-entry/v1"]
    entry_id: Digest
    study_id: Digest
    sequence: PositiveInt
    previous_entry_sha256: Digest
    operation_id: Digest
    event: Literal["completed"]
    protocol_id: Digest
    execution_id: Digest
    claim_entry_id: Digest
    artifact: StudyArtifactReference
    claim_authority: Literal["descriptive-only", "confirmatory"]

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        if self.artifact.kind != "evidence-pack":
            raise ValueError("completed Study requires an evidence artifact")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("Study ledger entry identity does not match canonical content")
        return self


StudyLedgerEntry = Annotated[
    PreparedLedgerEntry
    | AwaitingRevealLedgerEntry
    | AwaitingAcceptanceLedgerEntry
    | BoundRunningLedgerEntry
    | ExecutionStepClaimedLedgerEntry
    | ExecutionStepAdvancedLedgerEntry
    | PausedLedgerEntry
    | TerminatedLedgerEntry
    | CompletedLedgerEntry,
    Field(discriminator="event"),
]
_STUDY_LEDGER_ENTRY_ADAPTER: TypeAdapter[StudyLedgerEntry] = TypeAdapter(StudyLedgerEntry)


class RequestRevealDirective(StrictContract):
    schema_version: Literal["cernora.reference.advance-directive/v1"]
    action: Literal["request-reveal"]


class BindRevealDirective(StrictContract):
    schema_version: Literal["cernora.reference.advance-directive/v1"]
    action: Literal["bind-reveal"]
    reveal: HeldoutReveal


class StartExecutionDirective(StrictContract):
    schema_version: Literal["cernora.reference.advance-directive/v1"]
    action: Literal["start-execution"]
    acceptance_id: Digest
    run_plan: ControlledRunPlanV2
    comparison_plan: ComparisonPlanV1


class StepExecutionDirective(StrictContract):
    schema_version: Literal["cernora.reference.advance-directive/v1"]
    action: Literal["step-execution"]
    expected_state_id: Digest


AdvanceDirective = Annotated[
    RequestRevealDirective | BindRevealDirective | StartExecutionDirective | StepExecutionDirective,
    Field(discriminator="action"),
]
_ADVANCE_DIRECTIVE_ADAPTER: TypeAdapter[AdvanceDirective] = TypeAdapter(AdvanceDirective)


class StudyArtifactFile(StrictContract):
    path: Annotated[StrictStr, Field(min_length=1)]
    byte_length: NonNegativeInt
    sha256: Digest

    @model_validator(mode="after")
    def closed_relative_path(self) -> Self:
        validate_relative_path(self.path)
        if self.path == "manifest.json":
            raise ValueError("Study artifact manifest cannot index itself")
        return self


class StudyArtifactReport(StrictContract):
    schema_version: Literal["cernora.reference.study-artifact-report/v1"]
    report_id: Digest
    study_id: Digest
    protocol_id: Digest
    execution_id: Digest
    terminal_status: Literal["paused", "completed", "terminated"]
    reason: ConfirmatoryStopReason | None
    planned_trial_count: PositiveInt
    completed_trial_count: NonNegativeInt
    attempt_count: NonNegativeInt

    @model_validator(mode="after")
    def canonical_identity_and_status(self) -> Self:
        if (self.terminal_status == "completed") != (self.reason is None):
            raise ValueError("only a completed Study omits its terminal reason")
        if self.completed_trial_count > self.planned_trial_count:
            raise ValueError("Study artifact completed Trial count exceeds its Protocol")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"report_id"})
        )
        if self.report_id != expected:
            raise ValueError("Study artifact report identity does not match canonical content")
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
    files: tuple[StudyArtifactFile, ...] = Field(min_length=1)

    @field_validator("files", mode="before")
    @classmethod
    def tuple_files(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def coherent_authority_and_identity(self) -> Self:
        paths = tuple(item.path for item in self.files)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("Study artifact file index must be sorted and unique")
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


def _materialize_study_artifact_report(
    payload_without_identity: Mapping[str, object],
) -> StudyArtifactReport:
    payload = dict(payload_without_identity)
    payload["report_id"] = canonical_content_id(payload, excluded=frozenset())
    return StudyArtifactReport.model_validate(payload)


def _verify_study_artifact(
    root: Path,
    *,
    require_identity_name: bool = True,
) -> StudyArtifactManifest:
    files = closed_regular_tree(root)
    manifest = _load_canonical_contract(root / "manifest.json", StudyArtifactManifest)
    expected = {"manifest.json", *(item.path for item in manifest.files)}
    if set(files) != expected or (require_identity_name and root.name != manifest.artifact_id):
        raise ContractError("Study artifact file closure does not match its manifest")
    for item in manifest.files:
        path = root / item.path
        if path.stat().st_size != item.byte_length or sha256_file(path) != item.sha256:
            raise ContractError("Study artifact indexed file does not match its digest")
    report = _load_canonical_contract(root / "report.json", StudyArtifactReport)
    if (
        sha256_file(root / "report.json") != manifest.report_sha256
        or report.protocol_id != manifest.protocol_id
        or report.terminal_status != manifest.terminal_status
    ):
        raise ContractError("Study artifact report does not match its manifest")
    ledger_paths = sorted(item.path for item in manifest.files if item.path.startswith("ledger/"))
    if not ledger_paths or sha256_file(root / ledger_paths[-1]) != manifest.ledger_root_sha256:
        raise ContractError("Study artifact does not close its ledger prefix")
    if manifest.terminal_status == "completed":
        verify_execution_pack(root / "execution-pack")
        if (
            _execution_snapshot_id(root / "batch-summary") != manifest.batch_package_sha256
            or _execution_snapshot_id(root / "comparison") != manifest.comparison_package_sha256
        ):
            raise ContractError("Study Evidence Pack Core packages do not match its manifest")
    elif any(
        item.path.startswith("batch-summary/") or item.path.startswith("comparison/")
        for item in manifest.files
    ):
        raise ContractError("Study diagnostic artifact carries authoritative Core packages")
    return manifest


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


def _require_durable_destination(
    destination: Path, *, phase: ControlledStudyPhase = "prepare"
) -> Path:
    resolved = destination.resolve()
    if any(resolved == root or resolved.is_relative_to(root) for root in _temporary_roots()):
        raise ControlledStudyError("invalid-intent", phase=phase)
    try:
        parent_metadata = resolved.parent.lstat()
    except OSError as exc:
        raise ControlledStudyError("invalid-intent", phase=phase) from exc
    if not stat.S_ISDIR(parent_metadata.st_mode) or resolved.parent.is_symlink():
        raise ControlledStudyError("invalid-intent", phase=phase)
    return resolved


def _load_canonical_ledger_entry(path: Path) -> StudyLedgerEntry:
    payload = load_json_file(path)
    entry = _STUDY_LEDGER_ENTRY_ADAPTER.validate_python(payload)
    if read_regular_file_bytes(path) != _canonical_contract_bytes(entry):
        raise ContractError(f"{path.name} is not canonical JSON")
    return entry


def _execution_snapshot_id(root: Path) -> Digest:
    files = closed_regular_tree(root)
    return canonical_content_id(
        {
            "files": [
                {"path": relative, "sha256": sha256_file(path)} for relative, path in files.items()
            ]
        },
        excluded=frozenset(),
    )


def _ledger_outcome(entry: StudyLedgerEntry, ledger_root_sha256: Digest) -> ExecutionOutcome:
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.execution-outcome/v1",
        "study_id": entry.study_id,
        "protocol_id": entry.protocol_id,
        "ledger_root_sha256": ledger_root_sha256,
        "status": entry.event,
    }
    if isinstance(entry, AwaitingAcceptanceLedgerEntry):
        payload["acceptance_id"] = entry.acceptance_id
    elif isinstance(
        entry,
        BoundRunningLedgerEntry
        | ExecutionStepClaimedLedgerEntry
        | ExecutionStepAdvancedLedgerEntry,
    ):
        payload["status"] = "running"
        payload["execution_id"] = entry.execution_id
    elif isinstance(entry, PausedLedgerEntry):
        payload.update(
            {
                "execution_id": entry.execution_id,
                "reason": entry.reason,
                "artifact": entry.artifact.model_dump(mode="json"),
                "resumable": True,
            }
        )
    elif isinstance(entry, TerminatedLedgerEntry):
        payload.update(
            {
                "execution_id": entry.execution_id,
                "reason": entry.reason,
                "artifact": entry.artifact.model_dump(mode="json"),
                "resumable": False,
            }
        )
    elif isinstance(entry, CompletedLedgerEntry):
        payload.update(
            {
                "execution_id": entry.execution_id,
                "artifact": entry.artifact.model_dump(mode="json"),
                "claim_authority": entry.claim_authority,
            }
        )
    return materialize_execution_outcome(payload)


@dataclass(frozen=True)
class _ReplayedStudy:
    intent: StudyIntent
    protocol: StudyProtocol
    record: StudyRecord
    entries: tuple[StudyLedgerEntry, ...]
    outcomes: tuple[ExecutionOutcome, ...]


def _load_study(root: Path) -> _ReplayedStudy:
    files = closed_regular_tree(root)
    base_files = {
        path: file
        for path, file in files.items()
        if not path.startswith("execution/")
        and not path.startswith("execution-pack/")
        and not path.startswith("artifacts/")
    }
    execution_files = {path: file for path, file in files.items() if path.startswith("execution/")}
    execution_pack_files = {
        path: file for path, file in files.items() if path.startswith("execution-pack/")
    }
    ledger_paths = sorted(path for path in base_files if path.startswith("ledger/"))
    expected_ledger_paths = [
        f"ledger/{sequence:08d}.json" for sequence in range(1, len(ledger_paths) + 1)
    ]
    if not ledger_paths or ledger_paths != expected_ledger_paths:
        raise ContractError("Controlled Study ledger paths are not contiguous")
    expected_files = {
        ".writer.lock",
        "intent.json",
        "protocol.json",
        "study.json",
        *ledger_paths,
    }
    if set(base_files) != expected_files:
        raise ContractError("Controlled Study root contains unknown files")
    directories = {
        Path(directory).relative_to(root).as_posix()
        for directory, _, _ in os.walk(root, topdown=True, followlinks=False)
    }
    if not {".", "ledger"}.issubset(directories) or any(
        item not in {".", "ledger", "artifacts"}
        and item != "execution"
        and item != "execution-pack"
        and not item.startswith("execution/")
        and not item.startswith("execution-pack/")
        and not item.startswith("artifacts/")
        for item in directories
    ):
        raise ContractError("Controlled Study root contains unknown directories")
    if read_regular_file_bytes(root / ".writer.lock", maximum=0) != b"":
        raise ContractError("Controlled Study writer lock is malformed")
    intent = _load_canonical_contract(root / "intent.json", StudyIntent)
    protocol = _load_canonical_contract(root / "protocol.json", StudyProtocol)
    record = _load_canonical_contract(root / "study.json", StudyRecord)
    if protocol != compile_study_protocol(intent):
        raise ContractError("stored Study Protocol is not derived from its Intent")
    if (
        record.intent_id != intent.intent_id
        or record.protocol_id != protocol.protocol_id
        or record.implementation_lock_id != protocol.implementation_lock_id
        or record.intent_sha256 != sha256_file(root / "intent.json")
        or record.protocol_sha256 != sha256_file(root / "protocol.json")
    ):
        raise ContractError("prepared Controlled Study authority mismatch")

    entries: list[StudyLedgerEntry] = []
    outcomes: list[ExecutionOutcome] = []
    previous_sha256: Digest | None = None
    for sequence, relative in enumerate(ledger_paths, start=1):
        path = root / relative
        entry = _load_canonical_ledger_entry(path)
        if (
            entry.sequence != sequence
            or entry.study_id != record.study_id
            or entry.protocol_id != protocol.protocol_id
            or entry.previous_entry_sha256 != previous_sha256
        ):
            raise ContractError("Controlled Study ledger chain is inconsistent")
        if sequence == 1:
            if not isinstance(entry, PreparedLedgerEntry) or entry.operation_id != record.study_id:
                raise ContractError("Controlled Study ledger must begin with its prepared fact")
        else:
            prior = entries[-1]
            valid_transition = (
                (
                    isinstance(prior, PreparedLedgerEntry)
                    and isinstance(entry, AwaitingRevealLedgerEntry)
                )
                or (
                    isinstance(prior, AwaitingRevealLedgerEntry)
                    and isinstance(entry, AwaitingAcceptanceLedgerEntry)
                )
                or (
                    isinstance(prior, AwaitingAcceptanceLedgerEntry)
                    and isinstance(entry, BoundRunningLedgerEntry)
                )
                or (
                    isinstance(
                        prior,
                        BoundRunningLedgerEntry
                        | ExecutionStepAdvancedLedgerEntry
                        | PausedLedgerEntry,
                    )
                    and isinstance(entry, ExecutionStepClaimedLedgerEntry)
                )
                or (
                    isinstance(prior, ExecutionStepClaimedLedgerEntry)
                    and isinstance(
                        entry,
                        ExecutionStepAdvancedLedgerEntry
                        | PausedLedgerEntry
                        | TerminatedLedgerEntry
                        | CompletedLedgerEntry,
                    )
                )
            )
            if not valid_transition:
                raise ContractError("Controlled Study ledger contains an invalid transition")
            if isinstance(entry, AwaitingAcceptanceLedgerEntry):
                expected_cases = tuple(item for item in intent.cases if item.split == "held-out")
                revealed_case_root = sha256_bytes(
                    canonical_json_bytes(
                        [item.model_dump(mode="json") for item in entry.reveal.cases]
                    )
                )
                expected_acceptance_id = canonical_content_id(
                    {
                        "implementation_lock_id": protocol.implementation_lock_id,
                        "ledger_root_sha256": previous_sha256,
                        "protocol_id": protocol.protocol_id,
                        "reveal_id": entry.reveal.reveal_id,
                        "study_id": record.study_id,
                    },
                    excluded=frozenset(),
                )
                if (
                    entry.reveal.commitment_id != intent.heldout_commitment.commitment_id
                    or entry.reveal.manifest_sha256 != intent.heldout_commitment.manifest_sha256
                    or entry.reveal.cases != expected_cases
                    or revealed_case_root != intent.heldout_commitment.case_commitment_root_sha256
                    or entry.acceptance_id != expected_acceptance_id
                ):
                    raise ContractError("held-out reveal does not match frozen authority")
            if (
                isinstance(prior, AwaitingAcceptanceLedgerEntry)
                and isinstance(entry, BoundRunningLedgerEntry)
                and entry.acceptance_id != prior.acceptance_id
            ):
                raise ContractError("running Study does not bind the fresh acceptance")
            if isinstance(entry, BoundRunningLedgerEntry):
                from cernora_reference_workflow.study_projection import bind_study_run_plan

                binding = bind_study_run_plan(intent, protocol, entry.run_plan)
                entry.comparison_plan.validate_run_plan(entry.run_plan)
                expected_splits = tuple((item.case_id, item.split) for item in intent.cases)
                actual_splits = tuple(
                    (item.case_id, item.split_id) for item in entry.comparison_plan.case_splits
                )
                if (
                    entry.binding_id != binding.binding_id
                    or entry.ordered_trial_slot_ids != binding.ordered_trial_slot_ids
                    or actual_splits != expected_splits
                    or entry.comparison_plan.primary_outcome.scope != "split"
                    or entry.comparison_plan.primary_outcome.split_id != "held-out"
                ):
                    raise ContractError(
                        "running Study authority does not match the frozen Protocol"
                    )
            if isinstance(entry, ExecutionStepClaimedLedgerEntry) and (
                not isinstance(
                    prior,
                    BoundRunningLedgerEntry | ExecutionStepAdvancedLedgerEntry | PausedLedgerEntry,
                )
                or entry.execution_id != prior.execution_id
                or entry.expected_state_id != outcomes[-1].state_id
            ):
                raise ContractError("Study execution step does not bind its prior state")
            if isinstance(entry, ExecutionStepAdvancedLedgerEntry) and (
                not isinstance(prior, ExecutionStepClaimedLedgerEntry)
                or entry.execution_id != prior.execution_id
                or entry.claim_entry_id != prior.entry_id
            ):
                raise ContractError("Study execution step result does not bind its claim")
            if isinstance(
                entry,
                PausedLedgerEntry | TerminatedLedgerEntry | CompletedLedgerEntry,
            ) and (
                not isinstance(prior, ExecutionStepClaimedLedgerEntry)
                or entry.execution_id != prior.execution_id
                or entry.claim_entry_id != prior.entry_id
            ):
                raise ContractError("Study terminal result does not bind its execution claim")
        if any(item.operation_id == entry.operation_id for item in entries):
            raise ContractError("Controlled Study ledger repeats an operation identity")
        current_sha256 = sha256_file(path)
        entries.append(entry)
        outcomes.append(_ledger_outcome(entry, current_sha256))
        previous_sha256 = current_sha256
    bound_entries = tuple(item for item in entries if isinstance(item, BoundRunningLedgerEntry))
    terminal_entries = tuple(
        item
        for item in entries
        if isinstance(item, PausedLedgerEntry | TerminatedLedgerEntry | CompletedLedgerEntry)
    )
    artifact_roots = tuple(
        sorted(
            (
                child
                for child in (root / "artifacts").iterdir()
                if child.is_dir() and not child.is_symlink()
            ),
            key=lambda item: item.name,
        )
        if (root / "artifacts").is_dir() and not (root / "artifacts").is_symlink()
        else ()
    )
    artifact_file_roots = {
        Path(relative).parts[1] for relative in files if relative.startswith("artifacts/")
    }
    if artifact_file_roots != {item.name for item in artifact_roots}:
        raise ContractError("Controlled Study artifact custody contains unknown files")
    artifacts = {item.artifact_id: item for item in map(_verify_study_artifact, artifact_roots)}
    referenced_artifact_ids = {item.artifact.artifact_id for item in terminal_entries}
    unreferenced = set(artifacts) - referenced_artifact_ids
    latest_entry = entries[-1]
    if unreferenced and (
        not isinstance(latest_entry, ExecutionStepClaimedLedgerEntry)
        or len(unreferenced) != 1
        or artifacts[next(iter(unreferenced))].ledger_root_sha256 != previous_sha256
    ):
        raise ContractError("Controlled Study contains an unclaimed terminal artifact")
    for terminal in terminal_entries:
        manifest = artifacts.get(terminal.artifact.artifact_id)
        artifact_root = root / "artifacts" / terminal.artifact.artifact_id
        report = _load_canonical_contract(
            artifact_root / "report.json",
            StudyArtifactReport,
        )
        if (
            manifest is None
            or manifest.kind != terminal.artifact.kind
            or manifest.ledger_root_sha256 != terminal.previous_entry_sha256
            or manifest.terminal_status != terminal.event
            or report.study_id != record.study_id
            or report.execution_id != terminal.execution_id
            or report.reason
            != (None if isinstance(terminal, CompletedLedgerEntry) else terminal.reason)
        ):
            raise ContractError("Study terminal ledger entry does not match its artifact")
        if isinstance(terminal, CompletedLedgerEntry) and (
            manifest.claim_authority != terminal.claim_authority
        ):
            raise ContractError("completed Study claim authority differs from its Evidence Pack")
        for name in ("intent.json", "protocol.json", "study.json"):
            if read_regular_file_bytes(
                artifact_root / "authority" / name,
                maximum=None,
            ) != read_regular_file_bytes(root / name, maximum=None):
                raise ContractError("Study artifact authority differs from its durable Study")
        artifact_ledger = sorted(
            path for path in closed_regular_tree(artifact_root) if path.startswith("ledger/")
        )
        expected_artifact_ledger = [
            f"ledger/{sequence:08d}.json" for sequence in range(1, terminal.sequence)
        ]
        if artifact_ledger != expected_artifact_ledger or any(
            read_regular_file_bytes(artifact_root / relative, maximum=None)
            != read_regular_file_bytes(root / relative, maximum=None)
            for relative in artifact_ledger
        ):
            raise ContractError("Study artifact ledger differs from its durable prefix")
    if execution_files or "execution" in directories:
        if len(bound_entries) != 1:
            raise ContractError("Repeat Runner Execution has no unique Study authority")
        bound = bound_entries[0]
        latest = entries[-1]
        execution = (
            reload_execution_for_reconciliation(root / "execution")
            if isinstance(latest, ExecutionStepClaimedLedgerEntry)
            or (
                isinstance(latest, PausedLedgerEntry)
                and latest.reason == "ambiguous-active-attempt"
            )
            else reload_execution(root / "execution")
        )
        if (
            execution.record.execution_id != bound.execution_id
            or execution.record.nonce != bound.execution_nonce
            or execution.run_plan != bound.run_plan
        ):
            raise ContractError("Repeat Runner Execution does not match the Study ledger")
        if isinstance(latest, ExecutionStepAdvancedLedgerEntry) and (
            _execution_snapshot_id(root / "execution") != latest.execution_snapshot_id
        ):
            raise ContractError("Repeat Runner Execution changed after its Study step result")
    elif bound_entries and not isinstance(entries[-1], BoundRunningLedgerEntry):
        raise ContractError("Repeat Runner Execution is missing from an advanced Study")
    if execution_pack_files or "execution-pack" in directories:
        if len(bound_entries) != 1:
            raise ContractError("Execution Pack has no unique Study authority")
        pack = verify_execution_pack(root / "execution-pack")
        if (
            pack.execution_id != bound_entries[0].execution_id
            or pack.run_plan_id != bound_entries[0].run_plan.run_plan_id
        ):
            raise ContractError("Execution Pack does not match the Controlled Study")

    return _ReplayedStudy(
        intent=intent,
        protocol=protocol,
        record=record,
        entries=tuple(entries),
        outcomes=tuple(outcomes),
    )


@contextmanager
def _study_writer(root: Path) -> Iterator[None]:
    path = root / ".writer.lock"
    try:
        before = path.lstat()
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
    except OSError as exc:
        raise ControlledStudyError("corrupt-ledger", phase="advance") from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        os.close(descriptor)
        raise ControlledStudyError("corrupt-ledger", phase="advance")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise ControlledStudyError("concurrent-writer", phase="advance") from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _publish_durable_file(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parents[1].parent
    )
    temporary = Path(temporary_name)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ControlledStudyError("concurrent-writer", phase="advance") from exc
        _sync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _discard_artifact_staging(root: Path) -> None:
    artifacts = root / "artifacts"
    if not artifacts.exists():
        return
    if not artifacts.is_dir() or artifacts.is_symlink():
        raise ContractError("Controlled Study artifact custody is unsafe")
    for child in artifacts.iterdir():
        if child.name.startswith(".staging-"):
            if not child.is_dir() or child.is_symlink():
                raise ContractError("Controlled Study artifact staging is unsafe")
            shutil.rmtree(child)
    _sync_directory(artifacts)


def _copy_closed_tree(source: Path, destination: Path, *, prefix: str) -> None:
    for relative, path in closed_regular_tree(source).items():
        target = destination / prefix / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_durable_file(target, read_regular_file_bytes(path, maximum=None))


def _publish_diagnostic_artifact(
    root: Path,
    replayed: _ReplayedStudy,
    *,
    terminal_status: Literal["paused", "terminated"],
    reason: ConfirmatoryStopReason,
) -> StudyArtifactManifest:
    execution_root = root / "execution"
    execution = reload_execution_for_reconciliation(execution_root)
    before = _execution_snapshot_id(execution_root)
    report = _materialize_study_artifact_report(
        {
            "schema_version": "cernora.reference.study-artifact-report/v1",
            "study_id": replayed.record.study_id,
            "protocol_id": replayed.protocol.protocol_id,
            "execution_id": execution.record.execution_id,
            "terminal_status": terminal_status,
            "reason": reason,
            "planned_trial_count": execution.record.planned_trial_count,
            "completed_trial_count": len(execution.trial_manifests),
            "attempt_count": len(execution.active_attempts),
        }
    )
    artifacts = root / "artifacts"
    if not artifacts.exists():
        artifacts.mkdir(mode=0o700)
        _sync_directory(root)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=artifacts))
    try:
        authority = staging / "authority"
        authority.mkdir()
        for name in ("intent.json", "protocol.json", "study.json"):
            _write_durable_file(
                authority / name,
                read_regular_file_bytes(root / name, maximum=None),
            )
        ledger = staging / "ledger"
        ledger.mkdir()
        for entry in replayed.entries:
            name = f"{entry.sequence:08d}.json"
            _write_durable_file(
                ledger / name,
                read_regular_file_bytes(root / "ledger" / name, maximum=None),
            )
        _copy_closed_tree(execution_root, staging, prefix="execution")
        _write_durable_file(staging / "report.json", _canonical_contract_bytes(report))
        if _execution_snapshot_id(execution_root) != before:
            raise ContractError("Execution changed while publishing its diagnostic artifact")
        indexed = tuple(
            StudyArtifactFile(
                path=relative,
                byte_length=path.stat().st_size,
                sha256=sha256_file(path),
            )
            for relative, path in closed_regular_tree(staging).items()
        )
        manifest = materialize_study_artifact_manifest(
            {
                "schema_version": "cernora.reference.study-artifact-manifest/v1",
                "kind": "diagnostic-pack",
                "protocol_id": replayed.protocol.protocol_id,
                "implementation_lock_id": replayed.protocol.implementation_lock_id,
                "ledger_root_sha256": replayed.outcomes[-1].ledger_root_sha256,
                "terminal_status": terminal_status,
                "claim_authority": "diagnostic-only",
                "batch_package_sha256": None,
                "comparison_package_sha256": None,
                "report_sha256": sha256_file(staging / "report.json"),
                "files": [item.model_dump(mode="json") for item in indexed],
            }
        )
        _write_durable_file(staging / "manifest.json", _canonical_contract_bytes(manifest))
        destination = artifacts / manifest.artifact_id
        if destination.exists():
            if _verify_study_artifact(destination) != manifest:
                raise ContractError("preexisting Study artifact does not match terminal state")
            shutil.rmtree(staging)
        else:
            atomic_publish_directory(staging, destination)
        _sync_directory(artifacts)
        return _verify_study_artifact(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _publish_evidence_artifact(
    root: Path,
    replayed: _ReplayedStudy,
) -> StudyArtifactManifest:
    bound = next(item for item in replayed.entries if isinstance(item, BoundRunningLedgerEntry))
    execution_root = root / "execution"
    pack_root = root / "execution-pack"
    execution = reload_execution(execution_root)
    pack = verify_execution_pack(pack_root)
    if execution.manifest is None or pack.execution_id != execution.record.execution_id:
        raise ContractError("completed Study lacks its closed Execution and Pack")
    before = _execution_snapshot_id(pack_root)
    report = _materialize_study_artifact_report(
        {
            "schema_version": "cernora.reference.study-artifact-report/v1",
            "study_id": replayed.record.study_id,
            "protocol_id": replayed.protocol.protocol_id,
            "execution_id": execution.record.execution_id,
            "terminal_status": "completed",
            "reason": None,
            "planned_trial_count": execution.record.planned_trial_count,
            "completed_trial_count": len(execution.trial_manifests),
            "attempt_count": len(execution.active_attempts),
        }
    )
    artifacts = root / "artifacts"
    if not artifacts.exists():
        artifacts.mkdir(mode=0o700)
        _sync_directory(root)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=artifacts))
    try:
        authority = staging / "authority"
        authority.mkdir()
        for name in ("intent.json", "protocol.json", "study.json"):
            _write_durable_file(
                authority / name,
                read_regular_file_bytes(root / name, maximum=None),
            )
        _write_durable_file(authority / "run-plan.json", bound.run_plan.canonical_bytes())
        _write_durable_file(
            authority / "comparison-plan.json",
            bound.comparison_plan.canonical_bytes(),
        )
        ledger = staging / "ledger"
        ledger.mkdir()
        for entry in replayed.entries:
            name = f"{entry.sequence:08d}.json"
            _write_durable_file(
                ledger / name,
                read_regular_file_bytes(root / "ledger" / name, maximum=None),
            )
        _copy_closed_tree(pack_root, staging, prefix="execution-pack")
        batch_root = staging / "batch-summary"
        summarize_execution_pack(staging / "execution-pack", batch_root)
        comparison_root = staging / "comparison"
        compare_batch_summary(
            batch_root,
            authority / "run-plan.json",
            authority / "comparison-plan.json",
            comparison_root,
        )
        _write_durable_file(staging / "report.json", _canonical_contract_bytes(report))
        if _execution_snapshot_id(pack_root) != before:
            raise ContractError("Execution Pack changed while publishing Study evidence")
        batch_sha256 = _execution_snapshot_id(batch_root)
        comparison_sha256 = _execution_snapshot_id(comparison_root)
        indexed = tuple(
            StudyArtifactFile(
                path=relative,
                byte_length=path.stat().st_size,
                sha256=sha256_file(path),
            )
            for relative, path in closed_regular_tree(staging).items()
        )
        manifest = materialize_study_artifact_manifest(
            {
                "schema_version": "cernora.reference.study-artifact-manifest/v1",
                "kind": "evidence-pack",
                "protocol_id": replayed.protocol.protocol_id,
                "implementation_lock_id": replayed.protocol.implementation_lock_id,
                "ledger_root_sha256": replayed.outcomes[-1].ledger_root_sha256,
                "terminal_status": "completed",
                "claim_authority": replayed.protocol.claims.effect_conclusion,
                "batch_package_sha256": batch_sha256,
                "comparison_package_sha256": comparison_sha256,
                "report_sha256": sha256_file(staging / "report.json"),
                "files": [item.model_dump(mode="json") for item in indexed],
            }
        )
        _write_durable_file(staging / "manifest.json", _canonical_contract_bytes(manifest))
        destination = artifacts / manifest.artifact_id
        if destination.exists():
            if _verify_study_artifact(destination) != manifest:
                raise ContractError("preexisting Study Evidence Pack does not match completion")
            shutil.rmtree(staging)
        else:
            atomic_publish_directory(staging, destination)
        _sync_directory(artifacts)
        return _verify_study_artifact(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def rebuild(artifact_root: Path, destination: Path) -> StudyArtifactManifest:
    """Offline-verify one terminal artifact and reproduce its exact closed bytes."""

    try:
        artifact = _verify_study_artifact(artifact_root)
    except (ContractError, OSError, ValueError) as exc:
        raise ControlledStudyError("incomplete-pack", phase="rebuild") from exc
    if not destination.parent.is_dir() or destination.exists() or destination.is_symlink():
        raise ControlledStudyError(
            "destination-conflict",
            phase="rebuild",
            artifact_id=artifact.artifact_id,
        )
    try:
        if artifact.kind == "evidence-pack":
            with tempfile.TemporaryDirectory(
                prefix="cernora-study-rebuild-",
                dir=destination.parent,
            ) as temporary:
                validation = Path(temporary)
                rebuild_execution_pack(
                    artifact_root / "execution-pack",
                    validation / "execution",
                )
                summarize_execution_pack(
                    artifact_root / "execution-pack",
                    validation / "batch-summary",
                )
                compare_batch_summary(
                    validation / "batch-summary",
                    artifact_root / "authority" / "run-plan.json",
                    artifact_root / "authority" / "comparison-plan.json",
                    validation / "comparison",
                )
                if (
                    _execution_snapshot_id(validation / "batch-summary")
                    != artifact.batch_package_sha256
                    or _execution_snapshot_id(validation / "comparison")
                    != artifact.comparison_package_sha256
                ):
                    raise ContractError(
                        "offline-rebuilt Core packages differ from the Study Evidence Pack"
                    )
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent)
        )
        try:
            _copy_closed_tree(artifact_root, staging, prefix="")
            atomic_publish_directory(staging, destination)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        rebuilt = _verify_study_artifact(destination, require_identity_name=False)
        if rebuilt != artifact or _execution_snapshot_id(destination) != _execution_snapshot_id(
            artifact_root
        ):
            raise ContractError("offline-rebuilt Study artifact differs from its source")
        return rebuilt
    except ControlledStudyError:
        raise
    except (ContractError, OSError, ValueError) as exc:
        raise ControlledStudyError(
            "incomplete-pack",
            phase="rebuild",
            artifact_id=artifact.artifact_id,
        ) from exc


def prepare(intent: StudyIntent, destination: Path) -> PreparedOutcome:
    """Freeze and durably publish one Controlled Study without external work."""

    root = _require_durable_destination(destination)
    if root.exists() or root.is_symlink():
        try:
            with _study_writer(root):
                _discard_artifact_staging(root)
                replayed = _load_study(root)
        except (ContractError, OSError, ValueError) as exc:
            raise ControlledStudyError("corrupt-ledger", phase="prepare") from exc
        if replayed.intent.intent_id != intent.intent_id:
            raise ControlledStudyError(
                "destination-conflict",
                phase="prepare",
                artifact_id=replayed.record.study_id,
            )
        prepared = replayed.outcomes[0]
        if not isinstance(prepared, PreparedOutcome):
            raise ControlledStudyError("corrupt-ledger", phase="prepare")
        return prepared

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
        replayed = _load_study(root)
    except (ContractError, OSError, ValueError) as exc:
        raise ControlledStudyError("corrupt-ledger", phase="prepare") from exc
    prepared = replayed.outcomes[0]
    if not isinstance(prepared, PreparedOutcome):
        raise ControlledStudyError("corrupt-ledger", phase="prepare")
    return prepared


def _directive_operation_id(study_id: Digest, directive: AdvanceDirective) -> Digest:
    return canonical_content_id(
        {"directive": directive.model_dump(mode="json"), "study_id": study_id},
        excluded=frozenset(),
    )


def _ensure_bound_execution(root: Path, entry: BoundRunningLedgerEntry) -> None:
    execution_root = root / "execution"
    if not execution_root.exists() and not execution_root.is_symlink():
        initialize_execution(execution_root, entry.run_plan, nonce=entry.execution_nonce)
    execution = reload_execution(execution_root)
    if (
        execution.record.execution_id != entry.execution_id
        or execution.record.nonce != entry.execution_nonce
        or execution.run_plan != entry.run_plan
    ):
        raise ContractError("Repeat Runner Execution does not match the Study ledger")


def _advance_claimed_step(
    root: Path,
    replayed: _ReplayedStudy,
    claim: ExecutionStepClaimedLedgerEntry,
    executor: AttemptExecutor | None,
    should_stop: StopPredicate | None,
) -> ExecutionOutcome:
    if executor is None:
        raise ControlledStudyError("invalid-intent", phase="advance")
    execution_root = root / "execution"
    before = _execution_snapshot_id(execution_root)
    try:
        outcome = advance_repeat(
            execution_root,
            executor,
            pack_root=root / "execution-pack",
            should_stop=should_stop,
            allow_new_attempt=before == claim.prior_execution_snapshot_id,
        )
    except AmbiguousActiveAttempt:
        return _close_diagnostic_terminal(
            root,
            replayed,
            claim,
            event="paused",
            reason="ambiguous-active-attempt",
        )
    if outcome.status == "stopped":
        return _close_diagnostic_terminal(
            root,
            replayed,
            claim,
            event="paused",
            reason="operator-request",
        )
    if outcome.status == "budget-exhausted":
        return _close_diagnostic_terminal(
            root,
            replayed,
            claim,
            event="terminated",
            reason="budget-exhausted",
        )
    if outcome.status == "completed":
        try:
            return _close_completed_terminal(root, replayed, claim)
        except (ContractError, OSError, ValueError):
            return _close_diagnostic_terminal(
                root,
                replayed,
                claim,
                event="terminated",
                reason="integrity-failure",
            )
    if outcome.status != "running":
        raise ControlledStudyError("incomplete-pack", phase="advance")
    snapshot_id = _execution_snapshot_id(execution_root)
    operation_id = canonical_content_id(
        {"claim_entry_id": claim.entry_id, "event": "execution-step-advanced"},
        excluded=frozenset(),
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.study-ledger-entry/v1",
        "study_id": replayed.record.study_id,
        "sequence": len(replayed.entries) + 1,
        "previous_entry_sha256": replayed.outcomes[-1].ledger_root_sha256,
        "operation_id": operation_id,
        "event": "execution-step-advanced",
        "protocol_id": replayed.protocol.protocol_id,
        "execution_id": claim.execution_id,
        "claim_entry_id": claim.entry_id,
        "runner_status": outcome.status,
        "execution_snapshot_id": snapshot_id,
    }
    payload["entry_id"] = canonical_content_id(payload, excluded=frozenset())
    entry = _STUDY_LEDGER_ENTRY_ADAPTER.validate_python(payload)
    _publish_durable_file(
        root / "ledger" / f"{entry.sequence:08d}.json",
        _canonical_contract_bytes(entry),
    )
    try:
        advanced = _load_study(root)
    except (ContractError, OSError, ValueError) as exc:
        raise ControlledStudyError("corrupt-ledger", phase="advance") from exc
    return advanced.outcomes[-1]


def _close_diagnostic_terminal(
    root: Path,
    replayed: _ReplayedStudy,
    claim: ExecutionStepClaimedLedgerEntry,
    *,
    event: Literal["paused", "terminated"],
    reason: ConfirmatoryStopReason,
) -> ExecutionOutcome:
    artifact = _publish_diagnostic_artifact(
        root,
        replayed,
        terminal_status=event,
        reason=reason,
    )
    operation_id = canonical_content_id(
        {"artifact_id": artifact.artifact_id, "claim_entry_id": claim.entry_id, "event": event},
        excluded=frozenset(),
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.study-ledger-entry/v1",
        "study_id": replayed.record.study_id,
        "sequence": len(replayed.entries) + 1,
        "previous_entry_sha256": replayed.outcomes[-1].ledger_root_sha256,
        "operation_id": operation_id,
        "event": event,
        "protocol_id": replayed.protocol.protocol_id,
        "execution_id": claim.execution_id,
        "claim_entry_id": claim.entry_id,
        "reason": reason,
        "artifact": {"kind": artifact.kind, "artifact_id": artifact.artifact_id},
    }
    payload["entry_id"] = canonical_content_id(payload, excluded=frozenset())
    entry = _STUDY_LEDGER_ENTRY_ADAPTER.validate_python(payload)
    _publish_durable_file(
        root / "ledger" / f"{entry.sequence:08d}.json",
        _canonical_contract_bytes(entry),
    )
    try:
        closed = _load_study(root)
    except (ContractError, OSError, ValueError) as exc:
        raise ControlledStudyError("corrupt-ledger", phase="advance") from exc
    return closed.outcomes[-1]


def _close_completed_terminal(
    root: Path,
    replayed: _ReplayedStudy,
    claim: ExecutionStepClaimedLedgerEntry,
) -> ExecutionOutcome:
    artifact = _publish_evidence_artifact(root, replayed)
    operation_id = canonical_content_id(
        {
            "artifact_id": artifact.artifact_id,
            "claim_entry_id": claim.entry_id,
            "event": "completed",
        },
        excluded=frozenset(),
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.study-ledger-entry/v1",
        "study_id": replayed.record.study_id,
        "sequence": len(replayed.entries) + 1,
        "previous_entry_sha256": replayed.outcomes[-1].ledger_root_sha256,
        "operation_id": operation_id,
        "event": "completed",
        "protocol_id": replayed.protocol.protocol_id,
        "execution_id": claim.execution_id,
        "claim_entry_id": claim.entry_id,
        "artifact": {"kind": artifact.kind, "artifact_id": artifact.artifact_id},
        "claim_authority": artifact.claim_authority,
    }
    payload["entry_id"] = canonical_content_id(payload, excluded=frozenset())
    entry = _STUDY_LEDGER_ENTRY_ADAPTER.validate_python(payload)
    _publish_durable_file(
        root / "ledger" / f"{entry.sequence:08d}.json",
        _canonical_contract_bytes(entry),
    )
    try:
        closed = _load_study(root)
    except (ContractError, OSError, ValueError) as exc:
        raise ControlledStudyError("corrupt-ledger", phase="advance") from exc
    return closed.outcomes[-1]


def advance(
    root: Path,
    directive: AdvanceDirective | Mapping[str, object],
    *,
    executor: AttemptExecutor | None = None,
    should_stop: StopPredicate | None = None,
) -> ExecutionOutcome:
    """Idempotently advance one durable Study by at most one external Attempt."""

    destination = _require_durable_destination(root, phase="advance")
    try:
        parsed = _ADVANCE_DIRECTIVE_ADAPTER.validate_python(directive)
    except ValueError as exc:
        raise ControlledStudyError("invalid-intent", phase="advance") from exc

    with _study_writer(destination):
        try:
            _discard_artifact_staging(destination)
            replayed = _load_study(destination)
        except (ContractError, OSError, ValueError) as exc:
            raise ControlledStudyError("corrupt-ledger", phase="advance") from exc
        operation_id = _directive_operation_id(replayed.record.study_id, parsed)
        for index, entry in enumerate(replayed.entries):
            if entry.operation_id == operation_id:
                if isinstance(entry, BoundRunningLedgerEntry):
                    try:
                        _ensure_bound_execution(destination, entry)
                    except (ContractError, OSError, ValueError) as exc:
                        raise ControlledStudyError("corrupt-ledger", phase="advance") from exc
                if isinstance(entry, ExecutionStepClaimedLedgerEntry):
                    if index + 1 < len(replayed.entries):
                        result = replayed.entries[index + 1]
                        if not isinstance(
                            result,
                            ExecutionStepAdvancedLedgerEntry
                            | PausedLedgerEntry
                            | TerminatedLedgerEntry
                            | CompletedLedgerEntry,
                        ) or (result.claim_entry_id != entry.entry_id):
                            raise ControlledStudyError("corrupt-ledger", phase="advance")
                        return replayed.outcomes[index + 1]
                    return _advance_claimed_step(
                        destination,
                        replayed,
                        entry,
                        executor,
                        should_stop,
                    )
                return replayed.outcomes[index]

        current = replayed.entries[-1]
        previous_sha256 = replayed.outcomes[-1].ledger_root_sha256
        common: dict[str, object] = {
            "schema_version": "cernora.reference.study-ledger-entry/v1",
            "study_id": replayed.record.study_id,
            "sequence": len(replayed.entries) + 1,
            "previous_entry_sha256": previous_sha256,
            "operation_id": operation_id,
            "protocol_id": replayed.protocol.protocol_id,
        }
        if isinstance(parsed, RequestRevealDirective):
            if not isinstance(current, PreparedLedgerEntry):
                raise ControlledStudyError("invalid-transition", phase="advance")
            payload = {**common, "event": "awaiting-reveal"}
        elif isinstance(parsed, BindRevealDirective):
            if not isinstance(current, AwaitingRevealLedgerEntry):
                raise ControlledStudyError("invalid-transition", phase="advance")
            expected_cases = tuple(
                item for item in replayed.intent.cases if item.split == "held-out"
            )
            revealed_case_root = sha256_bytes(
                canonical_json_bytes([item.model_dump(mode="json") for item in parsed.reveal.cases])
            )
            if (
                parsed.reveal.commitment_id != replayed.intent.heldout_commitment.commitment_id
                or parsed.reveal.manifest_sha256
                != replayed.intent.heldout_commitment.manifest_sha256
                or parsed.reveal.cases != expected_cases
                or revealed_case_root
                != replayed.intent.heldout_commitment.case_commitment_root_sha256
            ):
                raise ControlledStudyError(
                    "authority-mismatch",
                    phase="advance",
                    artifact_id=parsed.reveal.reveal_id,
                )
            acceptance_id = canonical_content_id(
                {
                    "implementation_lock_id": replayed.protocol.implementation_lock_id,
                    "ledger_root_sha256": previous_sha256,
                    "protocol_id": replayed.protocol.protocol_id,
                    "reveal_id": parsed.reveal.reveal_id,
                    "study_id": replayed.record.study_id,
                },
                excluded=frozenset(),
            )
            payload = {
                **common,
                "event": "awaiting-acceptance",
                "reveal": parsed.reveal.model_dump(mode="json"),
                "acceptance_id": acceptance_id,
            }
        elif isinstance(parsed, StartExecutionDirective):
            if not isinstance(current, AwaitingAcceptanceLedgerEntry):
                raise ControlledStudyError("invalid-transition", phase="advance")
            if parsed.acceptance_id != current.acceptance_id:
                raise ControlledStudyError(
                    "stale-acceptance",
                    phase="advance",
                    artifact_id=current.acceptance_id,
                )
            try:
                from cernora_reference_workflow.study_projection import bind_study_run_plan

                binding = bind_study_run_plan(
                    replayed.intent,
                    replayed.protocol,
                    parsed.run_plan,
                )
                parsed.comparison_plan.validate_run_plan(parsed.run_plan)
                expected_splits = tuple(
                    (item.case_id, item.split) for item in replayed.intent.cases
                )
                actual_splits = tuple(
                    (item.case_id, item.split_id) for item in parsed.comparison_plan.case_splits
                )
                if (
                    actual_splits != expected_splits
                    or parsed.comparison_plan.primary_outcome.scope != "split"
                    or parsed.comparison_plan.primary_outcome.split_id != "held-out"
                ):
                    raise ContractError("ComparisonPlan does not preserve Study claim scopes")
            except (ContractError, ValueError) as exc:
                raise ControlledStudyError(
                    "authority-mismatch",
                    phase="advance",
                    artifact_id=parsed.run_plan.run_plan_id,
                ) from exc
            execution_nonce = secrets.token_hex(32)
            execution_id = canonical_content_id(
                {"nonce": execution_nonce, "run_plan_id": parsed.run_plan.run_plan_id},
                excluded=frozenset(),
            )
            payload = {
                **common,
                "event": "execution-started",
                "acceptance_id": parsed.acceptance_id,
                "execution_nonce": execution_nonce,
                "execution_id": execution_id,
                "binding_id": binding.binding_id,
                "ordered_trial_slot_ids": binding.ordered_trial_slot_ids,
                "run_plan": parsed.run_plan.model_dump(mode="json"),
                "comparison_plan": parsed.comparison_plan.model_dump(mode="json"),
            }
        else:
            if executor is None:
                raise ControlledStudyError("invalid-intent", phase="advance")
            if not isinstance(
                current,
                BoundRunningLedgerEntry | ExecutionStepAdvancedLedgerEntry | PausedLedgerEntry,
            ):
                raise ControlledStudyError("invalid-transition", phase="advance")
            if parsed.expected_state_id != replayed.outcomes[-1].state_id:
                raise ControlledStudyError(
                    "invalid-transition",
                    phase="advance",
                    artifact_id=replayed.outcomes[-1].state_id,
                )
            payload = {
                **common,
                "event": "execution-step-claimed",
                "execution_id": current.execution_id,
                "expected_state_id": parsed.expected_state_id,
                "prior_execution_snapshot_id": _execution_snapshot_id(destination / "execution"),
            }
        payload["entry_id"] = canonical_content_id(payload, excluded=frozenset())
        entry = _STUDY_LEDGER_ENTRY_ADAPTER.validate_python(payload)
        path = destination / "ledger" / f"{entry.sequence:08d}.json"
        _publish_durable_file(path, _canonical_contract_bytes(entry))
        try:
            advanced = _load_study(destination)
        except (ContractError, OSError, ValueError) as exc:
            raise ControlledStudyError("corrupt-ledger", phase="advance") from exc
        if advanced.entries[-1].operation_id != operation_id:
            raise ControlledStudyError("corrupt-ledger", phase="advance")
        latest = advanced.entries[-1]
        if isinstance(latest, BoundRunningLedgerEntry):
            try:
                _ensure_bound_execution(destination, latest)
                advanced = _load_study(destination)
            except (ContractError, OSError, ValueError) as exc:
                raise ControlledStudyError("corrupt-ledger", phase="advance") from exc
        elif isinstance(latest, ExecutionStepClaimedLedgerEntry):
            return _advance_claimed_step(
                destination,
                advanced,
                latest,
                executor,
                should_stop,
            )
        return advanced.outcomes[-1]


__all__ = [
    "AdvanceDirective",
    "AwaitingAcceptanceOutcome",
    "AwaitingRevealOutcome",
    "BaselineAuthority",
    "CandidateDevelopmentRecord",
    "CandidateHypothesis",
    "CandidatePatch",
    "CompletedOutcome",
    "ControlledStudyError",
    "ControlledStudyErrorCode",
    "ControlledStudyPhase",
    "ExecutionOutcome",
    "HeldoutCommitment",
    "HeldoutReveal",
    "ImplementationArtifact",
    "ImplementationLock",
    "PausedOutcome",
    "PreparedOutcome",
    "RunningOutcome",
    "StartExecutionDirective",
    "StepExecutionDirective",
    "StudyAnalysisPolicy",
    "StudyArtifactManifest",
    "StudyArtifactReference",
    "StudyCaseAuthority",
    "StudyClaimPolicy",
    "StudyIntent",
    "StudyPairBlock",
    "StudyProtocol",
    "StudyRecord",
    "TerminatedOutcome",
    "advance",
    "compile_study_protocol",
    "materialize_execution_outcome",
    "materialize_heldout_commitment",
    "materialize_heldout_reveal",
    "materialize_implementation_lock",
    "materialize_study_analysis_policy",
    "materialize_study_artifact_manifest",
    "materialize_study_intent",
    "prepare",
    "rebuild",
]
