"""Strict append-only storage for one Priority 4 Milestone 1 execution."""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from cernora import read_imported_evaluation
from pydantic import Field, StrictInt, model_validator

from cernora_reference_workflow.attempt_record import verify_preterminal_attempt
from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
    sha256_file,
    validate_relative_path,
)
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    verify_controlled_attempt_artifact,
)
from cernora_reference_workflow.controlled_experiment_spec import ControlledExperimentSpecV2
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    ControlledTrialSlotV2,
)
from cernora_reference_workflow.experiment_spec import Digest, NonEmpty, StrictContract
from cernora_reference_workflow.export import verify_completed_export
from cernora_reference_workflow.lifecycle import (
    TerminalRecord,
    TerminalState,
    select_terminal_attempt,
)
from cernora_reference_workflow.profile import create_profile
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.report import (
    OfflineRebuildAvailable,
    RunReport,
    StrictReloadVerified,
)
from cernora_reference_workflow.run_plan import ConnectorIdentity, RunPlan, TrialSlot

PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
ExecutionRunPlan = RunPlan | ControlledRunPlanV2
ExecutionSlotAuthority = Annotated[
    TrialSlot | ControlledTrialSlotV2,
    Field(discriminator="schema_version"),
]


class ExecutionRecord(StrictContract):
    schema_version: Literal["cernora.reference.execution/v1"]
    execution_id: Digest
    run_plan_id: Digest
    nonce: Digest
    run_plan_sha256: Digest
    trial_slots_sha256: Digest
    planned_trial_count: PositiveInt
    companion_version: Literal["0.2.0", "0.3.0", "0.4.0", "0.4.2"]
    connector: ConnectorIdentity

    @model_validator(mode="after")
    def validate_identity(self) -> ExecutionRecord:
        expected = canonical_content_id(
            {"nonce": self.nonce, "run_plan_id": self.run_plan_id}, excluded=frozenset()
        )
        if self.execution_id != expected:
            raise ValueError("execution_id does not bind the RunPlan and nonce")
        return self


class ExecutionTrialSlot(StrictContract):
    schema_version: Literal["cernora.reference.execution-trial-slot/v1"]
    execution_id: Digest
    trial_id: Digest
    slot: ExecutionSlotAuthority

    @model_validator(mode="after")
    def validate_identity(self) -> ExecutionTrialSlot:
        expected = canonical_content_id(
            {"execution_id": self.execution_id, "trial_slot_id": self.slot.trial_slot_id},
            excluded=frozenset(),
        )
        if self.trial_id != expected:
            raise ValueError("trial_id does not bind its Execution and Trial slot")
        return self


class ExecutionTrialSlots(StrictContract):
    schema_version: Literal["cernora.reference.execution-trial-slots/v1"]
    execution_id: Digest
    run_plan_id: Digest
    slots: Annotated[tuple[ExecutionTrialSlot, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_slots(self) -> ExecutionTrialSlots:
        if any(item.execution_id != self.execution_id for item in self.slots):
            raise ValueError("Execution Trial slot binds another Execution")
        trial_ids = tuple(item.trial_id for item in self.slots)
        slot_ids = tuple(item.slot.trial_slot_id for item in self.slots)
        if len(trial_ids) != len(set(trial_ids)) or len(slot_ids) != len(set(slot_ids)):
            raise ValueError("Execution Trial slots must be unique")
        return self


class ActiveAttemptRecord(StrictContract):
    schema_version: Literal["cernora.reference.active-attempt/v1"]
    active_record_id: Digest
    execution_id: Digest
    run_plan_id: Digest
    trial_id: Digest
    trial_slot_id: Digest
    experiment_id: Digest
    ordinal: PositiveInt
    predecessor_attempt_id: Digest | None
    elapsed_before_attempt_milliseconds: NonNegativeInt
    started_unix_milliseconds: NonNegativeInt
    wall_deadline_unix_milliseconds: PositiveInt

    @model_validator(mode="after")
    def validate_identity(self) -> ActiveAttemptRecord:
        if self.wall_deadline_unix_milliseconds <= self.started_unix_milliseconds:
            raise ValueError("active Attempt wall deadline must follow its start")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"active_record_id"})
        )
        if self.active_record_id != expected:
            raise ValueError("active Attempt record identity mismatch")
        return self


class AttemptBinding(StrictContract):
    ordinal: PositiveInt
    active_record_id: Digest
    attempt_id: Digest
    predecessor_attempt_id: Digest | None
    source_trial_id: NonEmpty
    artifact_kind: Literal["completed-export", "preterminal", "controlled-attempt"]
    artifact_manifest_sha256: Digest
    terminal_sha256: Digest


class ClosedFile(StrictContract):
    path: NonEmpty
    byte_length: Annotated[StrictInt, Field(ge=0)]
    sha256: Digest

    @model_validator(mode="after")
    def validate_path(self) -> ClosedFile:
        validate_relative_path(self.path)
        return self


class TrialEvaluationAvailable(StrictContract):
    status: Literal["available"]
    evaluation_id: NonEmpty
    evidence_id: NonEmpty
    score_id: NonEmpty
    decision_id: NonEmpty
    evaluation_input_sha256: Digest
    files: Annotated[tuple[ClosedFile, ...], Field(min_length=1)]


class TrialEvaluationUnavailable(StrictContract):
    status: Literal["unavailable"]


TrialEvaluation = Annotated[
    TrialEvaluationAvailable | TrialEvaluationUnavailable,
    Field(discriminator="status"),
]


class TrialResultManifest(StrictContract):
    schema_version: Literal["cernora.reference.trial-result/v1"]
    result_id: Digest
    execution_id: Digest
    run_plan_id: Digest
    trial_id: Digest
    experiment_id: Digest
    selected_attempt_id: Digest
    run_report_id: Digest
    run_report_sha256: Digest
    evaluation: TrialEvaluation

    @model_validator(mode="after")
    def validate_identity(self) -> TrialResultManifest:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"result_id"})
        )
        if self.result_id != expected:
            raise ValueError("Trial result identity mismatch")
        return self


class ControlledTrialResultManifest(StrictContract):
    """Immutable V2 Trial result without a legacy task-specific RunReport."""

    schema_version: Literal["cernora.reference.controlled-trial-result/v1"]
    result_id: Digest
    execution_id: Digest
    run_plan_id: Digest
    trial_id: Digest
    experiment_id: Digest
    selected_attempt_id: Digest
    selected_attempt_artifact_id: Digest
    selected_attempt_sha256: Digest
    evaluation_status: Literal["evaluated", "unavailable"]

    @model_validator(mode="after")
    def validate_identity(self) -> ControlledTrialResultManifest:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"result_id"})
        )
        if self.result_id != expected:
            raise ValueError("controlled Trial result identity mismatch")
        return self


TrialResultAuthority = TrialResultManifest | ControlledTrialResultManifest


class TrialManifest(StrictContract):
    schema_version: Literal["cernora.reference.trial-manifest/v1"]
    trial_manifest_id: Digest
    execution_id: Digest
    run_plan_id: Digest
    trial_id: Digest
    trial_slot_id: Digest
    experiment_id: Digest
    selected_attempt_id: Digest
    terminal_state: TerminalState
    result_id: Digest
    result_manifest_sha256: Digest
    attempts: Annotated[tuple[AttemptBinding, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_identity_and_chain(self) -> TrialManifest:
        if tuple(item.ordinal for item in self.attempts) != tuple(range(1, len(self.attempts) + 1)):
            raise ValueError("Trial Attempt ordinals must be contiguous")
        if len({item.attempt_id for item in self.attempts}) != len(self.attempts):
            raise ValueError("Trial Attempt IDs must be unique")
        if self.selected_attempt_id != self.attempts[-1].attempt_id:
            raise ValueError("Trial must select its final Attempt")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"trial_manifest_id"})
        )
        if self.trial_manifest_id != expected:
            raise ValueError("Trial Manifest identity mismatch")
        return self


class TrialReference(StrictContract):
    trial_id: Digest
    trial_manifest_sha256: Digest


class ActiveAttemptReference(StrictContract):
    trial_id: Digest
    ordinal: PositiveInt
    active_record_id: Digest
    active_record_sha256: Digest


class ExecutionCheckpoint(StrictContract):
    schema_version: Literal["cernora.reference.execution-checkpoint/v1"]
    checkpoint_id: Digest
    execution_id: Digest
    run_plan_id: Digest
    sequence: PositiveInt
    previous_checkpoint_sha256: Digest | None
    status: Literal["running", "stopped", "budget-exhausted", "completed"]
    elapsed_milliseconds: Annotated[StrictInt, Field(ge=0)]
    attempt_count: Annotated[StrictInt, Field(ge=0)]
    completed_trials: tuple[TrialReference, ...]
    active_attempts: tuple[ActiveAttemptReference, ...]

    @model_validator(mode="after")
    def validate_identity_and_uniqueness(self) -> ExecutionCheckpoint:
        if len({item.trial_id for item in self.completed_trials}) != len(self.completed_trials):
            raise ValueError("checkpoint contains duplicate completed Trials")
        keys = tuple((item.trial_id, item.ordinal) for item in self.active_attempts)
        if len(keys) != len(set(keys)):
            raise ValueError("checkpoint contains duplicate active Attempts")
        if self.attempt_count != len(self.active_attempts):
            raise ValueError("checkpoint Attempt count does not match immutable active records")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"checkpoint_id"})
        )
        if self.checkpoint_id != expected:
            raise ValueError("checkpoint identity mismatch")
        return self


class ExecutionManifest(StrictContract):
    schema_version: Literal["cernora.reference.execution-manifest/v1"]
    execution_manifest_id: Digest
    execution_id: Digest
    run_plan_id: Digest
    run_plan_sha256: Digest
    trial_slots_sha256: Digest
    latest_checkpoint_sha256: Digest
    diagnostic_json_sha256: Digest
    diagnostic_markdown_sha256: Digest
    trials: Annotated[tuple[TrialReference, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_identity_and_uniqueness(self) -> ExecutionManifest:
        if len({item.trial_id for item in self.trials}) != len(self.trials):
            raise ValueError("Execution Manifest contains duplicate Trials")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"execution_manifest_id"})
        )
        if self.execution_manifest_id != expected:
            raise ValueError("Execution Manifest identity mismatch")
        return self


class DiagnosticTrial(StrictContract):
    slot_index: PositiveInt
    trial_id: Digest
    trial_slot_id: Digest
    lifecycle_state: TerminalState
    attempt_count: PositiveInt
    result_status: Literal["evaluated", "unavailable"]


class ExecutionDiagnostic(StrictContract):
    schema_version: Literal["cernora.reference.execution-diagnostic/v1"]
    diagnostic_id: Digest
    execution_id: Digest
    run_plan_id: Digest
    execution_status: Literal["completed"]
    planned_trial_count: PositiveInt
    completed_trial_count: PositiveInt
    attempt_count: PositiveInt
    trials: Annotated[tuple[DiagnosticTrial, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_identity_and_counts(self) -> ExecutionDiagnostic:
        if self.completed_trial_count != len(self.trials):
            raise ValueError("diagnostic completed Trial count mismatch")
        if self.attempt_count != sum(item.attempt_count for item in self.trials):
            raise ValueError("diagnostic Attempt count mismatch")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"diagnostic_id"})
        )
        if self.diagnostic_id != expected:
            raise ValueError("diagnostic identity mismatch")
        return self

    def markdown(self) -> str:
        lines = [
            "# Cernora M1 execution diagnostic",
            "",
            "> Lifecycle and completeness facts only. `diagnostic.json` is authoritative.",
            "",
            f"- Execution: `{self.execution_id}`",
            f"- RunPlan: `{self.run_plan_id}`",
            f"- Status: `{self.execution_status}`",
            f"- Planned Trials: {self.planned_trial_count}",
            f"- Completed Trials: {self.completed_trial_count}",
            f"- Attempts: {self.attempt_count}",
            "",
            "| Slot | Trial | Lifecycle | Attempts | Result artifacts |",
            "| ---: | --- | --- | ---: | --- |",
        ]
        lines.extend(
            f"| {item.slot_index} | `{item.trial_id}` | `{item.lifecycle_state}` | "
            f"{item.attempt_count} | `{item.result_status}` |"
            for item in self.trials
        )
        lines.append("")
        return "\n".join(lines)


class ExecutionPackManifest(StrictContract):
    schema_version: Literal["cernora.reference.execution-pack/v1"]
    pack_id: Digest
    execution_id: Digest
    run_plan_id: Digest
    files: Annotated[tuple[ClosedFile, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_identity_and_files(self) -> ExecutionPackManifest:
        paths = tuple(item.path for item in self.files)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("Execution Pack file index must be uniquely sorted")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"pack_id"})
        )
        if self.pack_id != expected:
            raise ValueError("Execution Pack identity mismatch")
        return self


@dataclass(frozen=True)
class _AttemptArtifact:
    terminal: TerminalRecord
    source_trial_id: str
    kind: Literal["completed-export", "preterminal", "controlled-attempt"]
    manifest_sha256: str
    terminal_sha256: str
    controlled_attempt: ControlledAttempt | None


@dataclass(frozen=True)
class ExecutionState:
    run_plan: ExecutionRunPlan
    record: ExecutionRecord
    trial_slots: ExecutionTrialSlots
    active_attempts: tuple[ActiveAttemptRecord, ...]
    trial_results: tuple[TrialResultAuthority, ...]
    trial_manifests: tuple[TrialManifest, ...]
    checkpoints: tuple[ExecutionCheckpoint, ...]
    adopted_trial_ids: tuple[str, ...]
    diagnostic: ExecutionDiagnostic | None
    manifest: ExecutionManifest | None


def _load_canonical[ModelT: StrictContract](path: Path, model: type[ModelT]) -> ModelT:
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise ContractError(f"{path.name} must contain a JSON object")
    parsed = model.model_validate(payload)
    if path.read_bytes() != canonical_json_bytes(parsed.model_dump(mode="json")):
        raise ContractError(f"{path.name} is not canonical JSON")
    return parsed


def _publish_file(path: Path, payload: bytes) -> None:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ContractError("publication parent must be an existing real directory")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ContractError(f"append-only destination already exists: {path.name}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _slot_set(plan: ExecutionRunPlan, execution_id: str) -> ExecutionTrialSlots:
    slots = tuple(
        ExecutionTrialSlot(
            schema_version="cernora.reference.execution-trial-slot/v1",
            execution_id=execution_id,
            trial_id=canonical_content_id(
                {"execution_id": execution_id, "trial_slot_id": slot.trial_slot_id},
                excluded=frozenset(),
            ),
            slot=slot,
        )
        for slot in plan.expand_trial_slots()
    )
    return ExecutionTrialSlots(
        schema_version="cernora.reference.execution-trial-slots/v1",
        execution_id=execution_id,
        run_plan_id=plan.run_plan_id,
        slots=slots,
    )


def initialize_execution(
    destination: Path, run_plan: ExecutionRunPlan, *, nonce: str | None = None
) -> ExecutionState:
    """Atomically freeze an Execution and its complete slot namespace."""

    if not destination.parent.is_dir() or destination.exists() or destination.is_symlink():
        raise ContractError("Execution destination parent must exist and destination must not")
    execution_nonce = nonce or secrets.token_hex(32)
    execution_id = canonical_content_id(
        {"nonce": execution_nonce, "run_plan_id": run_plan.run_plan_id}, excluded=frozenset()
    )
    slot_set = _slot_set(run_plan, execution_id)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        run_plan_path = staging / "run-plan.json"
        slots_path = staging / "trial-slots.json"
        run_plan_path.write_bytes(run_plan.canonical_bytes())
        slots_path.write_bytes(canonical_json_bytes(slot_set.model_dump(mode="json")))
        record = ExecutionRecord(
            schema_version="cernora.reference.execution/v1",
            execution_id=execution_id,
            run_plan_id=run_plan.run_plan_id,
            nonce=execution_nonce,
            run_plan_sha256=sha256_file(run_plan_path),
            trial_slots_sha256=sha256_file(slots_path),
            planned_trial_count=len(slot_set.slots),
            companion_version=run_plan.companion_version,
            connector=run_plan.connector,
        )
        (staging / "execution.json").write_bytes(
            canonical_json_bytes(record.model_dump(mode="json"))
        )
        for name in ("active-attempts", "attempts", "results", "trials", "checkpoints", "specs"):
            (staging / name).mkdir()
        for spec in run_plan.experiment_specs:
            (staging / "specs" / f"{spec.experiment_id}.json").write_bytes(spec.canonical_bytes())
        for item in slot_set.slots:
            (staging / "active-attempts" / item.trial_id).mkdir()
            (staging / "attempts" / item.trial_id).mkdir()
        atomic_publish_directory(staging, destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return reload_execution(destination)


def _expected_directories(root: Path, slots: ExecutionTrialSlots) -> set[Path]:
    expected = {
        root,
        *(
            root / name
            for name in (
                "active-attempts",
                "attempts",
                "results",
                "trials",
                "checkpoints",
                "specs",
            )
        ),
    }
    for item in slots.slots:
        expected.add(root / "active-attempts" / item.trial_id)
        expected.add(root / "attempts" / item.trial_id)
    return expected


def _verify_attempt(path: Path) -> _AttemptArtifact:
    manifest_payload = load_json_file(path / "manifest.json")
    if not isinstance(manifest_payload, dict):
        raise ContractError("Attempt manifest must contain a JSON object")
    schema = manifest_payload.get("schema_version")
    if schema == "cernora.reference.completed-export/v1":
        verified = verify_completed_export(path)
        verified_attempt_id = verified.attempt_id
        source_trial_id = verified.source_trial_id
        kind: Literal["completed-export", "preterminal", "controlled-attempt"] = "completed-export"
        controlled_attempt = None
    elif schema == "cernora.reference.preterminal-attempt/v1":
        verified_preterminal = verify_preterminal_attempt(path)
        verified_attempt_id = verified_preterminal.attempt_id
        source_trial_id = verified_preterminal.source_trial_id
        kind = "preterminal"
        controlled_attempt = None
    elif schema == "cernora.reference.controlled-attempt-artifact/v1":
        verified_controlled = verify_controlled_attempt_artifact(path)
        verified_attempt_id = verified_controlled.attempt.attempt_id
        source_trial_id = verified_controlled.attempt.trial_id
        kind = "controlled-attempt"
        controlled_attempt = verified_controlled.attempt
    else:
        raise ContractError("Attempt directory has an unknown artifact contract")
    terminal = _load_canonical(path / "terminal.json", TerminalRecord)
    if verified_attempt_id != terminal.attempt_id:
        raise ContractError("Attempt artifact and terminal identity mismatch")
    return _AttemptArtifact(
        terminal=terminal,
        source_trial_id=source_trial_id,
        kind=kind,
        manifest_sha256=sha256_file(path / "manifest.json"),
        terminal_sha256=sha256_file(path / "terminal.json"),
        controlled_attempt=controlled_attempt,
    )


def _active_path(root: Path, trial_id: str, ordinal: int) -> Path:
    return root / "active-attempts" / trial_id / f"{ordinal:04d}.json"


def _attempt_path(root: Path, trial_id: str, ordinal: int) -> Path:
    return root / "attempts" / trial_id / f"{ordinal:04d}"


def _trial_path(root: Path, trial_id: str) -> Path:
    return root / "trials" / f"{trial_id}.json"


def _result_path(root: Path, trial_id: str) -> Path:
    return root / "results" / trial_id


def _verify_legacy_trial_result(
    path: Path,
    *,
    record: ExecutionRecord,
    run_plan: ExecutionRunPlan,
    slot: ExecutionTrialSlot,
    active_records: list[ActiveAttemptRecord],
    artifacts: dict[tuple[str, int], _AttemptArtifact],
) -> TrialResultManifest:
    files = closed_regular_tree(path)
    if "manifest.json" not in files or "run-report.json" not in files:
        raise ContractError("Trial result is missing its manifest or RunReport")
    result = _load_canonical(files["manifest.json"], TrialResultManifest)
    report = RunReport.from_file(files["run-report.json"])
    attempt_artifacts = tuple(
        artifacts.get((slot.trial_id, active.ordinal)) for active in active_records
    )
    if not attempt_artifacts or any(item is None for item in attempt_artifacts):
        raise ContractError("Trial result requires a complete terminal Attempt chain")
    verified_artifacts = tuple(item for item in attempt_artifacts if item is not None)
    selected = select_terminal_attempt(tuple(item.terminal for item in verified_artifacts))
    if (
        result.execution_id != record.execution_id
        or result.run_plan_id != run_plan.run_plan_id
        or result.trial_id != slot.trial_id
        or result.experiment_id != slot.slot.experiment_id
        or result.selected_attempt_id != selected.attempt_id
        or result.run_report_id != report.report_id
        or result.run_report_sha256 != sha256_file(files["run-report.json"])
        or report.experiment_id != slot.slot.experiment_id
        or report.selected_attempt_id != selected.attempt_id
    ):
        raise ContractError("Trial result does not bind its frozen Trial, Attempt, and RunReport")
    expected_attempts = tuple(item.terminal.attempt_id for item in verified_artifacts)
    if tuple(item.attempt_id for item in report.attempts) != expected_attempts:
        raise ContractError("RunReport does not bind the complete Attempt chain")
    if tuple(item.source_trial_id for item in report.attempts) != tuple(
        item.source_trial_id for item in verified_artifacts
    ):
        raise ContractError("RunReport does not bind Attempt source Trial identities")
    for reported, artifact in zip(report.attempts, verified_artifacts, strict=True):
        if (
            reported.lifecycle_outcome != artifact.terminal.state
            or reported.predecessor_attempt_id != artifact.terminal.predecessor_attempt_id
            or reported.retry_eligible != artifact.terminal.retry_eligible
        ):
            raise ContractError("RunReport Attempt facts contradict terminal evidence")
    specification = next(
        item for item in run_plan.experiment_specs if item.experiment_id == slot.slot.experiment_id
    )
    if isinstance(report.offline_rebuild, OfflineRebuildAvailable) and (
        report.offline_rebuild.experiment_spec.path != f"specs/{specification.experiment_id}.json"
        or report.offline_rebuild.experiment_spec.sha256
        != sha256_file(path.parents[1] / "specs" / f"{specification.experiment_id}.json")
    ):
        raise ContractError("RunReport does not bind its frozen portable ExperimentSpec")
    evaluation_files = {
        name: file for name, file in files.items() if name.startswith("offline-evaluation/")
    }
    selected_kind = verified_artifacts[-1].kind
    if selected_kind == "completed-export" and not isinstance(
        result.evaluation, TrialEvaluationAvailable
    ):
        raise ContractError("selected completed export requires an available strict evaluation")
    if selected_kind == "preterminal" and not isinstance(
        result.evaluation, TrialEvaluationUnavailable
    ):
        raise ContractError("selected preterminal Attempt requires an unavailable result")
    if isinstance(result.evaluation, TrialEvaluationUnavailable):
        if evaluation_files or isinstance(report.evaluation.strict_reload, StrictReloadVerified):
            raise ContractError("unavailable Trial result contradicts evaluation artifacts")
        if (
            verified_artifacts[-1].kind == "preterminal"
            and report.evaluation.validity != "unavailable"
        ):
            raise ContractError("unavailable P3 Attempt requires an unavailable RunReport")
        expected_paths = {"manifest.json", "run-report.json"}
    else:
        strict = report.evaluation.strict_reload
        if not isinstance(strict, StrictReloadVerified):
            raise ContractError("available evaluation requires a verified RunReport reload")
        indexed = {f"offline-evaluation/{item.path}": item for item in result.evaluation.files}
        if set(indexed) != set(evaluation_files):
            raise ContractError("Trial evaluation file index does not close the tree")
        for relative, entry in indexed.items():
            file = files[relative]
            if file.stat().st_size != entry.byte_length or sha256_file(file) != entry.sha256:
                raise ContractError("Trial evaluation file digest mismatch")
        receipt = read_imported_evaluation(path / "offline-evaluation/evaluated", create_profile())
        identities = (
            receipt.evaluation_id,
            receipt.evidence_id,
            receipt.score_id,
            receipt.decision_id,
            receipt.evaluation_input_sha256,
        )
        expected_identities = (
            result.evaluation.evaluation_id,
            result.evaluation.evidence_id,
            result.evaluation.score_id,
            result.evaluation.decision_id,
            result.evaluation.evaluation_input_sha256,
        )
        report_identities = (
            strict.evaluation_id,
            strict.evidence_id,
            strict.score_id,
            strict.decision_id,
            strict.evaluation_input_sha256,
        )
        if identities != expected_identities or identities != report_identities:
            raise ContractError("Trial evaluation identities do not match strict reload")
        expected_paths = {"manifest.json", "run-report.json", *evaluation_files}
    if set(files) != expected_paths:
        raise ContractError("Trial result contains unknown files")
    return result


def _verify_controlled_trial_result(
    path: Path,
    *,
    record: ExecutionRecord,
    run_plan: ExecutionRunPlan,
    slot: ExecutionTrialSlot,
    active_records: list[ActiveAttemptRecord],
    artifacts: dict[tuple[str, int], _AttemptArtifact],
) -> ControlledTrialResultManifest:
    files = closed_regular_tree(path)
    if set(files) != {"manifest.json"}:
        raise ContractError("controlled Trial result has an invalid closed file set")
    result = _load_canonical(files["manifest.json"], ControlledTrialResultManifest)
    attempt_artifacts = tuple(
        artifacts.get((slot.trial_id, active.ordinal)) for active in active_records
    )
    if not attempt_artifacts or any(item is None for item in attempt_artifacts):
        raise ContractError("controlled Trial result requires a complete Attempt chain")
    verified_artifacts = tuple(item for item in attempt_artifacts if item is not None)
    if any(item.controlled_attempt is None for item in verified_artifacts):
        raise ContractError("controlled Trial result requires only V2 ControlledAttempts")
    selected = verified_artifacts[-1]
    controlled = verify_controlled_attempt_artifact(
        _attempt_path(path.parents[1], slot.trial_id, len(verified_artifacts))
    )
    if (
        result.execution_id != record.execution_id
        or result.run_plan_id != run_plan.run_plan_id
        or result.trial_id != slot.trial_id
        or result.experiment_id != slot.slot.experiment_id
        or result.selected_attempt_id != selected.terminal.attempt_id
        or result.selected_attempt_artifact_id != controlled.manifest.artifact_id
        or result.selected_attempt_sha256
        != sha256_file(
            _attempt_path(path.parents[1], slot.trial_id, len(verified_artifacts)) / "attempt.json"
        )
        or result.evaluation_status
        != ("evaluated" if controlled.attempt.evaluation is not None else "unavailable")
    ):
        raise ContractError("controlled Trial result does not bind its frozen Attempt")
    return result


def _verify_trial_result(
    path: Path,
    *,
    record: ExecutionRecord,
    run_plan: ExecutionRunPlan,
    slot: ExecutionTrialSlot,
    active_records: list[ActiveAttemptRecord],
    artifacts: dict[tuple[str, int], _AttemptArtifact],
) -> TrialResultAuthority:
    payload = load_json_file(path / "manifest.json")
    if not isinstance(payload, dict):
        raise ContractError("Trial result manifest must contain one JSON object")
    if payload.get("schema_version") == "cernora.reference.controlled-trial-result/v1":
        return _verify_controlled_trial_result(
            path,
            record=record,
            run_plan=run_plan,
            slot=slot,
            active_records=active_records,
            artifacts=artifacts,
        )
    return _verify_legacy_trial_result(
        path,
        record=record,
        run_plan=run_plan,
        slot=slot,
        active_records=active_records,
        artifacts=artifacts,
    )


def _build_diagnostic(
    record: ExecutionRecord,
    run_plan: ExecutionRunPlan,
    slots: ExecutionTrialSlots,
    trials: tuple[TrialManifest, ...],
    results: dict[str, TrialResultAuthority],
) -> ExecutionDiagnostic:
    trials_by_id = {item.trial_id: item for item in trials}

    def result_status(result: TrialResultAuthority) -> Literal["evaluated", "unavailable"]:
        if isinstance(result, TrialResultManifest) and isinstance(
            result.evaluation, TrialEvaluationAvailable
        ):
            return "evaluated"
        if isinstance(result, ControlledTrialResultManifest):
            return result.evaluation_status
        return "unavailable"

    diagnostic_trials = tuple(
        DiagnosticTrial(
            slot_index=slot.slot.slot_index,
            trial_id=slot.trial_id,
            trial_slot_id=slot.slot.trial_slot_id,
            lifecycle_state=trials_by_id[slot.trial_id].terminal_state,
            attempt_count=len(trials_by_id[slot.trial_id].attempts),
            result_status=result_status(results[slot.trial_id]),
        )
        for slot in slots.slots
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.execution-diagnostic/v1",
        "execution_id": record.execution_id,
        "run_plan_id": run_plan.run_plan_id,
        "execution_status": "completed",
        "planned_trial_count": len(slots.slots),
        "completed_trial_count": len(diagnostic_trials),
        "attempt_count": sum(item.attempt_count for item in diagnostic_trials),
        "trials": [item.model_dump(mode="json") for item in diagnostic_trials],
    }
    payload["diagnostic_id"] = canonical_content_id(payload, excluded=frozenset())
    return ExecutionDiagnostic.model_validate(payload)


def _load_run_plan(path: Path) -> ExecutionRunPlan:
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise ContractError("RunPlan must contain one JSON object")
    if payload.get("schema_version") == "cernora.reference.controlled-run-plan/v2":
        return ControlledRunPlanV2.from_file(path)
    return RunPlan.from_file(path)


def _load_state(root: Path, *, allow_ambiguous: bool) -> ExecutionState:
    run_plan = _load_run_plan(root / "run-plan.json")
    record = _load_canonical(root / "execution.json", ExecutionRecord)
    slot_set = _load_canonical(root / "trial-slots.json", ExecutionTrialSlots)
    expected_slots = _slot_set(run_plan, record.execution_id)
    if slot_set != expected_slots:
        raise ContractError("Execution Trial slots do not exactly match the frozen RunPlan")
    if (
        record.run_plan_id != run_plan.run_plan_id
        or record.run_plan_sha256 != sha256_file(root / "run-plan.json")
        or record.trial_slots_sha256 != sha256_file(root / "trial-slots.json")
        or record.planned_trial_count != len(slot_set.slots)
        or record.companion_version != run_plan.companion_version
        or record.connector != run_plan.connector
    ):
        raise ContractError("Execution record does not bind its frozen initialization")

    slot_by_trial = {item.trial_id: item for item in slot_set.slots}
    expected_directories = _expected_directories(root, slot_set)
    diagnostic_root = root / "diagnostic"
    if diagnostic_root.exists():
        if not diagnostic_root.is_dir() or diagnostic_root.is_symlink():
            raise ContractError("Execution diagnostic must be a real directory")
        expected_directories.add(diagnostic_root)
    files = closed_regular_tree(root)
    observed_directories = {root}
    for directory, directories, _ in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        observed_directories.add(parent)
        observed_directories.update(parent / name for name in directories)
    attempt_roots: set[Path] = set()
    for item in slot_set.slots:
        parent = root / "attempts" / item.trial_id
        for child in parent.iterdir():
            if not child.name.isdigit() or len(child.name) != 4 or not child.is_dir():
                raise ContractError("Attempt namespace contains an unknown entry")
            attempt_roots.add(child)
    result_roots: set[Path] = set()
    for child in (root / "results").iterdir():
        if child.name not in slot_by_trial or not child.is_dir() or child.is_symlink():
            raise ContractError("Trial result namespace contains an unknown entry")
        result_roots.add(child)
    nested_roots = attempt_roots | result_roots
    allowed_nested = {
        directory
        for directory in observed_directories
        if any(directory == nested or nested in directory.parents for nested in nested_roots)
    }
    if observed_directories - allowed_nested != expected_directories:
        raise ContractError("Execution root contains an unknown or missing directory")

    root_files = {"run-plan.json", "execution.json", "trial-slots.json"}
    if "execution-manifest.json" in files:
        root_files.add("execution-manifest.json")
    expected_file_paths = {root / name for name in root_files}
    for specification in run_plan.experiment_specs:
        spec_path = root / "specs" / f"{specification.experiment_id}.json"
        if not spec_path.is_file() or spec_path.read_bytes() != specification.canonical_bytes():
            raise ContractError("portable ExperimentSpec does not match the frozen RunPlan")
        expected_file_paths.add(spec_path)
    active: list[ActiveAttemptRecord] = []
    artifacts: dict[tuple[str, int], _AttemptArtifact] = {}
    for item in slot_set.slots:
        for path in sorted((root / "active-attempts" / item.trial_id).iterdir()):
            if not path.is_file() or path.suffix != ".json" or not path.stem.isdigit():
                raise ContractError("active Attempt namespace contains an unknown entry")
            ordinal = int(path.stem)
            if path.name != f"{ordinal:04d}.json":
                raise ContractError("active Attempt filename is not canonical")
            active_record = _load_canonical(path, ActiveAttemptRecord)
            if (
                active_record.execution_id != record.execution_id
                or active_record.run_plan_id != run_plan.run_plan_id
                or active_record.trial_id != item.trial_id
                or active_record.trial_slot_id != item.slot.trial_slot_id
                or active_record.experiment_id != item.slot.experiment_id
                or active_record.ordinal != ordinal
            ):
                raise ContractError("active Attempt record does not bind its frozen Trial")
            wall_budget = run_plan.execution.max_total_wall_time_seconds * 1000
            if (
                active_record.elapsed_before_attempt_milliseconds >= wall_budget
                or active_record.wall_deadline_unix_milliseconds
                != active_record.started_unix_milliseconds
                + wall_budget
                - active_record.elapsed_before_attempt_milliseconds
            ):
                raise ContractError("active Attempt wall-time anchor contradicts the RunPlan")
            active.append(active_record)
            expected_file_paths.add(path)
            attempt = _attempt_path(root, item.trial_id, ordinal)
            if attempt.exists():
                artifact = _verify_attempt(attempt)
                expected_file_paths.update(closed_regular_tree(attempt).values())
                manifest_payload = load_json_file(attempt / "manifest.json")
                assert isinstance(manifest_payload, dict)
                if (
                    manifest_payload.get("experiment_id") != item.slot.experiment_id
                    or artifact.terminal.predecessor_attempt_id
                    != active_record.predecessor_attempt_id
                ):
                    raise ContractError("Attempt artifact does not bind its active Trial record")
                if artifact.controlled_attempt is not None:
                    specification = next(
                        specification
                        for specification in run_plan.experiment_specs
                        if specification.experiment_id == item.slot.experiment_id
                    )
                    if not isinstance(specification, ControlledExperimentSpecV2):
                        raise ContractError("controlled Attempt requires a controlled V2 plan")
                    artifact.controlled_attempt.verify_authority(specification)
                artifacts[(item.trial_id, ordinal)] = artifact
            elif not allow_ambiguous:
                raise ContractError("active Attempt has no verifiable terminal artifact")

    active.sort(key=lambda value: (slot_by_trial[value.trial_id].slot.slot_index, value.ordinal))
    grouped: dict[str, list[ActiveAttemptRecord]] = {item.trial_id: [] for item in slot_set.slots}
    for value in active:
        grouped[value.trial_id].append(value)
    for trial_id, records in grouped.items():
        if tuple(value.ordinal for value in records) != tuple(range(1, len(records) + 1)):
            raise ContractError("active Attempt ordinals are not contiguous")
        for index, value in enumerate(records):
            expected_predecessor = None
            if index:
                prior = artifacts.get((trial_id, index))
                if prior is None or not prior.terminal.retry_eligible:
                    raise ContractError("Attempt retry is not authorized by its predecessor")
                expected_predecessor = prior.terminal.attempt_id
            if value.predecessor_attempt_id != expected_predecessor:
                raise ContractError("active Attempt predecessor chain is broken")

    for attempt_root in attempt_roots:
        trial_id = attempt_root.parent.name
        ordinal = int(attempt_root.name)
        if (trial_id, ordinal) not in artifacts:
            raise ContractError("Attempt artifact has no immutable active record")

    trial_results: list[TrialResultAuthority] = []
    result_by_trial: dict[str, TrialResultAuthority] = {}
    for item in slot_set.slots:
        result_root = _result_path(root, item.trial_id)
        if not result_root.exists():
            continue
        result = _verify_trial_result(
            result_root,
            record=record,
            run_plan=run_plan,
            slot=item,
            active_records=grouped[item.trial_id],
            artifacts=artifacts,
        )
        trial_results.append(result)
        result_by_trial[item.trial_id] = result
        expected_file_paths.update(closed_regular_tree(result_root).values())

    trial_manifests: list[TrialManifest] = []
    for item in slot_set.slots:
        path = _trial_path(root, item.trial_id)
        if not path.exists():
            continue
        trial = _load_canonical(path, TrialManifest)
        if (
            trial.execution_id != record.execution_id
            or trial.run_plan_id != run_plan.run_plan_id
            or trial.trial_id != item.trial_id
            or trial.trial_slot_id != item.slot.trial_slot_id
            or trial.experiment_id != item.slot.experiment_id
        ):
            raise ContractError("Trial Manifest does not bind its frozen slot")
        bound_result = result_by_trial.get(item.trial_id)
        if (
            bound_result is None
            or trial.result_id != bound_result.result_id
            or trial.result_manifest_sha256
            != sha256_file(_result_path(root, item.trial_id) / "manifest.json")
        ):
            raise ContractError("Trial Manifest does not bind its immutable Trial result")
        records = grouped[item.trial_id]
        if len(trial.attempts) != len(records):
            raise ContractError("Trial Manifest does not bind the complete Attempt chain")
        terminals: list[TerminalRecord] = []
        for binding, active_record in zip(trial.attempts, records, strict=True):
            trial_artifact = artifacts.get((item.trial_id, active_record.ordinal))
            if trial_artifact is None:
                raise ContractError("Trial Manifest references an unterminated Attempt")
            expected_binding = AttemptBinding(
                ordinal=active_record.ordinal,
                active_record_id=active_record.active_record_id,
                attempt_id=trial_artifact.terminal.attempt_id,
                predecessor_attempt_id=trial_artifact.terminal.predecessor_attempt_id,
                source_trial_id=trial_artifact.source_trial_id,
                artifact_kind=trial_artifact.kind,
                artifact_manifest_sha256=trial_artifact.manifest_sha256,
                terminal_sha256=trial_artifact.terminal_sha256,
            )
            if binding != expected_binding:
                raise ContractError("Trial Manifest Attempt binding mismatch")
            terminals.append(trial_artifact.terminal)
        selected = select_terminal_attempt(tuple(terminals))
        if (
            selected.attempt_id != trial.selected_attempt_id
            or selected.state != trial.terminal_state
        ):
            raise ContractError("Trial Manifest terminal selection mismatch")
        trial_manifests.append(trial)
        expected_file_paths.add(path)

    checkpoint_paths = sorted((root / "checkpoints").iterdir())
    checkpoints: list[ExecutionCheckpoint] = []
    prior_digest: str | None = None
    prior_trials: set[str] = set()
    prior_active: set[tuple[str, int]] = set()
    prior_elapsed = 0
    prior_attempt_count = 0
    prior_status: Literal["running", "stopped", "budget-exhausted", "completed"] | None = None
    trial_by_id = {item.trial_id: item for item in trial_manifests}
    active_by_key = {(item.trial_id, item.ordinal): item for item in active}
    for sequence, checkpoint_path in enumerate(checkpoint_paths, start=1):
        if not checkpoint_path.is_file() or checkpoint_path.name != f"{sequence:08d}.json":
            raise ContractError("checkpoint sequence is not contiguous and canonical")
        checkpoint = _load_canonical(checkpoint_path, ExecutionCheckpoint)
        if (
            checkpoint.sequence != sequence
            or checkpoint.execution_id != record.execution_id
            or checkpoint.run_plan_id != run_plan.run_plan_id
            or checkpoint.previous_checkpoint_sha256 != prior_digest
        ):
            raise ContractError("checkpoint hash chain is broken")
        if prior_status in {"budget-exhausted", "completed"}:
            raise ContractError("terminal checkpoint status cannot append another snapshot")
        if (
            checkpoint.elapsed_milliseconds < prior_elapsed
            or checkpoint.attempt_count < prior_attempt_count
        ):
            raise ContractError("checkpoint resource snapshots are not monotonic")
        trial_ids = {reference.trial_id for reference in checkpoint.completed_trials}
        active_keys = {
            (reference.trial_id, reference.ordinal) for reference in checkpoint.active_attempts
        }
        if not prior_trials <= trial_ids or not prior_active <= active_keys:
            raise ContractError("checkpoint snapshot is not monotonic")
        all_trials_complete = trial_ids == set(slot_by_trial)
        attempt_budget_reached = checkpoint.attempt_count >= run_plan.execution.max_attempt_count
        wall_budget_reached = checkpoint.elapsed_milliseconds >= (
            run_plan.execution.max_total_wall_time_seconds * 1000
        )
        if checkpoint.status == "completed" and not all_trials_complete:
            raise ContractError("completed checkpoint requires every planned Trial")
        if checkpoint.status == "completed" and wall_budget_reached:
            raise ContractError("completed checkpoint cannot reach the frozen wall budget")
        if checkpoint.status == "budget-exhausted" and not (
            attempt_budget_reached or wall_budget_reached
        ):
            raise ContractError("budget-exhausted checkpoint has not reached a frozen budget")
        for trial_reference in checkpoint.completed_trials:
            checkpoint_trial = trial_by_id.get(trial_reference.trial_id)
            if checkpoint_trial is None or sha256_file(
                _trial_path(root, trial_reference.trial_id)
            ) != (trial_reference.trial_manifest_sha256):
                raise ContractError("checkpoint references a missing or corrupt Trial")
        for active_reference in checkpoint.active_attempts:
            active_value = active_by_key.get((active_reference.trial_id, active_reference.ordinal))
            active_path = _active_path(root, active_reference.trial_id, active_reference.ordinal)
            if (
                active_value is None
                or active_value.active_record_id != active_reference.active_record_id
                or sha256_file(active_path) != active_reference.active_record_sha256
            ):
                raise ContractError("checkpoint references a missing or corrupt active Attempt")
        checkpoints.append(checkpoint)
        expected_file_paths.add(checkpoint_path)
        prior_digest = sha256_file(checkpoint_path)
        prior_trials = trial_ids
        prior_active = active_keys
        prior_elapsed = checkpoint.elapsed_milliseconds
        prior_attempt_count = checkpoint.attempt_count
        prior_status = checkpoint.status

    latest_trials = prior_trials if checkpoints else set()
    adopted_ids = set(trial_by_id) - latest_trials
    adopted = tuple(item.trial_id for item in slot_set.slots if item.trial_id in adopted_ids)

    diagnostic: ExecutionDiagnostic | None = None
    diagnostic_path = root / "diagnostic/diagnostic.json"
    if diagnostic_path.exists():
        if len(trial_manifests) != len(slot_set.slots) or len(trial_results) != len(slot_set.slots):
            raise ContractError("Execution diagnostic requires every planned Trial result")
        diagnostic = _load_canonical(diagnostic_path, ExecutionDiagnostic)
        expected_diagnostic = _build_diagnostic(
            record,
            run_plan,
            slot_set,
            tuple(trial_manifests),
            result_by_trial,
        )
        if diagnostic != expected_diagnostic:
            raise ContractError("Execution diagnostic does not match lifecycle facts")
        expected_file_paths.update(closed_regular_tree(diagnostic_root).values())
        if set(closed_regular_tree(diagnostic_root)) != {"diagnostic.json", "diagnostic.md"}:
            raise ContractError("Execution diagnostic directory has an invalid closed file set")
        if (diagnostic_root / "diagnostic.md").read_text(encoding="utf-8") != diagnostic.markdown():
            raise ContractError("Execution diagnostic Markdown is not deterministic")

    manifest: ExecutionManifest | None = None
    manifest_path = root / "execution-manifest.json"
    if manifest_path.exists():
        manifest = _load_canonical(manifest_path, ExecutionManifest)
        expected_trials = tuple(
            TrialReference(
                trial_id=item.trial_id,
                trial_manifest_sha256=sha256_file(_trial_path(root, item.trial_id)),
            )
            for item in slot_set.slots
        )
        if (
            len(trial_manifests) != len(slot_set.slots)
            or not checkpoints
            or set(latest_trials) != set(slot_by_trial)
            or manifest.execution_id != record.execution_id
            or manifest.run_plan_id != run_plan.run_plan_id
            or manifest.run_plan_sha256 != record.run_plan_sha256
            or manifest.trial_slots_sha256 != record.trial_slots_sha256
            or manifest.latest_checkpoint_sha256 != prior_digest
            or prior_status != "completed"
            or diagnostic is None
            or manifest.diagnostic_json_sha256 != sha256_file(diagnostic_root / "diagnostic.json")
            or manifest.diagnostic_markdown_sha256 != sha256_file(diagnostic_root / "diagnostic.md")
            or manifest.trials != expected_trials
        ):
            raise ContractError("completed Execution Manifest is incomplete or inconsistent")

    if set(files.values()) - expected_file_paths:
        unexpected = sorted(
            path.relative_to(root).as_posix() for path in set(files.values()) - expected_file_paths
        )
        raise ContractError(f"Execution root contains unknown files: {unexpected}")
    return ExecutionState(
        run_plan=run_plan,
        record=record,
        trial_slots=slot_set,
        active_attempts=tuple(active),
        trial_results=tuple(trial_results),
        trial_manifests=tuple(trial_manifests),
        checkpoints=tuple(checkpoints),
        adopted_trial_ids=adopted,
        diagnostic=diagnostic,
        manifest=manifest,
    )


def reload_execution(root: Path) -> ExecutionState:
    """Strictly verify an Execution, adopting only verified uncheckpointed Trial terminals."""

    return _load_state(root, allow_ambiguous=False)


def reload_execution_for_reconciliation(root: Path) -> ExecutionState:
    """Verify an Execution while retaining an unclosed active Attempt for reconciliation."""

    return _load_state(root, allow_ambiguous=True)


def start_attempt(
    root: Path,
    trial_id: str,
    *,
    predecessor_attempt_id: str | None = None,
    elapsed_before_attempt_milliseconds: int | None = None,
    started_unix_milliseconds: int | None = None,
) -> ActiveAttemptRecord:
    state = _load_state(root, allow_ambiguous=True)
    if state.manifest is not None:
        raise ContractError("completed Execution cannot append an Attempt")
    slot = next((item for item in state.trial_slots.slots if item.trial_id == trial_id), None)
    if slot is None:
        raise ContractError("cannot start an Attempt for an unknown Trial")
    for active_attempt in state.active_attempts:
        if not _attempt_path(root, active_attempt.trial_id, active_attempt.ordinal).exists():
            raise ContractError("an active Attempt has no verifiable terminal artifact")
    completed_ids = {item.trial_id for item in state.trial_manifests}
    if trial_id in completed_ids:
        raise ContractError("completed Trial cannot append another Attempt")
    next_slot = next(
        (item for item in state.trial_slots.slots if item.trial_id not in completed_ids), None
    )
    if next_slot is None or trial_id != next_slot.trial_id:
        raise ContractError("Attempts must follow the frozen sequential Trial-slot order")
    records = tuple(item for item in state.active_attempts if item.trial_id == trial_id)
    if records and not _attempt_path(root, trial_id, records[-1].ordinal).exists():
        raise ContractError("prior active Attempt has no verifiable terminal artifact")
    if len(state.active_attempts) >= state.run_plan.execution.max_attempt_count:
        raise ContractError("Execution maximum Attempt budget is exhausted")
    specification = next(
        item
        for item in state.run_plan.experiment_specs
        if item.experiment_id == slot.slot.experiment_id
    )
    ordinal = len(records) + 1
    if ordinal > 1 + specification.retry.max_retries:
        raise ContractError("Trial retry limit is exhausted")
    expected_predecessor: str | None = None
    if records:
        prior = _verify_attempt(_attempt_path(root, trial_id, records[-1].ordinal)).terminal
        if not prior.retry_eligible:
            raise ContractError("prior terminal state is not retry eligible")
        expected_predecessor = prior.attempt_id
    if predecessor_attempt_id != expected_predecessor:
        raise ContractError("Attempt predecessor does not match the verified retry chain")
    elapsed_before = (
        elapsed_before_attempt_milliseconds
        if elapsed_before_attempt_milliseconds is not None
        else (state.checkpoints[-1].elapsed_milliseconds if state.checkpoints else 0)
    )
    wall_budget = state.run_plan.execution.max_total_wall_time_seconds * 1000
    if elapsed_before < 0 or elapsed_before >= wall_budget:
        raise ContractError("cannot start an Attempt after the frozen wall budget")
    if state.checkpoints and elapsed_before < state.checkpoints[-1].elapsed_milliseconds:
        raise ContractError("active Attempt elapsed anchor precedes the latest checkpoint")
    started_unix = (
        started_unix_milliseconds
        if started_unix_milliseconds is not None
        else int(time.time() * 1000)
    )
    if started_unix < 0:
        raise ContractError("active Attempt wall-clock anchor must be non-negative")
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.active-attempt/v1",
        "execution_id": state.record.execution_id,
        "run_plan_id": state.run_plan.run_plan_id,
        "trial_id": trial_id,
        "trial_slot_id": slot.slot.trial_slot_id,
        "experiment_id": slot.slot.experiment_id,
        "ordinal": ordinal,
        "predecessor_attempt_id": predecessor_attempt_id,
        "elapsed_before_attempt_milliseconds": elapsed_before,
        "started_unix_milliseconds": started_unix,
        "wall_deadline_unix_milliseconds": started_unix + wall_budget - elapsed_before,
    }
    payload["active_record_id"] = canonical_content_id(payload, excluded=frozenset())
    record = ActiveAttemptRecord.model_validate(payload)
    _publish_file(
        _active_path(root, trial_id, ordinal), canonical_json_bytes(record.model_dump(mode="json"))
    )
    return record


def publish_trial_result(
    root: Path,
    trial_id: str,
    *,
    report: RunReport,
    offline_evaluation_root: Path | None = None,
) -> TrialResultManifest:
    """Atomically publish the canonical report and optional strict offline evaluation tree."""

    state = reload_execution(root)
    if state.manifest is not None:
        raise ContractError("completed Execution cannot append a Trial result")
    slot = next((item for item in state.trial_slots.slots if item.trial_id == trial_id), None)
    if slot is None:
        raise ContractError("cannot publish a result for an unknown Trial")
    if _result_path(root, trial_id).exists():
        raise ContractError("Trial result destination must not already exist")
    records = [item for item in state.active_attempts if item.trial_id == trial_id]
    if not records:
        raise ContractError("Trial result requires at least one terminal Attempt")
    artifacts = {
        (trial_id, item.ordinal): _verify_attempt(_attempt_path(root, trial_id, item.ordinal))
        for item in records
    }
    selected = select_terminal_attempt(
        tuple(artifacts[(trial_id, item.ordinal)].terminal for item in records)
    )
    specification = next(
        item
        for item in state.run_plan.experiment_specs
        if item.experiment_id == slot.slot.experiment_id
    )
    if (
        artifacts[(trial_id, records[-1].ordinal)].terminal.retry_eligible
        and len(records) <= specification.retry.max_retries
    ):
        raise ContractError("retry-eligible Trial cannot publish a result before its frozen retry")
    staging = Path(tempfile.mkdtemp(prefix=f".{trial_id}.staging-", dir=root / "results"))
    try:
        (staging / "run-report.json").write_bytes(report.canonical_bytes())
        evaluation: TrialEvaluation
        if offline_evaluation_root is None:
            evaluation = TrialEvaluationUnavailable(status="unavailable")
        else:
            source_files = closed_regular_tree(offline_evaluation_root)
            if not source_files:
                raise ContractError("offline evaluation tree must be non-empty")
            shutil.copytree(offline_evaluation_root, staging / "offline-evaluation")
            strict = report.evaluation.strict_reload
            if not isinstance(strict, StrictReloadVerified):
                raise ContractError("offline evaluation requires a verified RunReport")
            entries = tuple(
                ClosedFile(
                    path=relative,
                    byte_length=path.stat().st_size,
                    sha256=sha256_file(path),
                )
                for relative, path in source_files.items()
            )
            evaluation = TrialEvaluationAvailable(
                status="available",
                evaluation_id=strict.evaluation_id,
                evidence_id=strict.evidence_id,
                score_id=strict.score_id,
                decision_id=strict.decision_id,
                evaluation_input_sha256=strict.evaluation_input_sha256,
                files=entries,
            )
        payload: dict[str, object] = {
            "schema_version": "cernora.reference.trial-result/v1",
            "execution_id": state.record.execution_id,
            "run_plan_id": state.run_plan.run_plan_id,
            "trial_id": trial_id,
            "experiment_id": slot.slot.experiment_id,
            "selected_attempt_id": selected.attempt_id,
            "run_report_id": report.report_id,
            "run_report_sha256": sha256_file(staging / "run-report.json"),
            "evaluation": evaluation.model_dump(mode="json"),
        }
        payload["result_id"] = canonical_content_id(payload, excluded=frozenset())
        result = TrialResultManifest.model_validate(payload)
        (staging / "manifest.json").write_bytes(
            canonical_json_bytes(result.model_dump(mode="json"))
        )
        _verify_trial_result(
            staging,
            record=state.record,
            run_plan=state.run_plan,
            slot=slot,
            active_records=records,
            artifacts=artifacts,
        )
        atomic_publish_directory(staging, _result_path(root, trial_id))
        return result
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def publish_controlled_trial_result(
    root: Path,
    trial_id: str,
) -> ControlledTrialResultManifest:
    """Atomically close one V2 Trial from its adopted ControlledAttempt chain."""

    state = reload_execution(root)
    if state.manifest is not None:
        raise ContractError("completed Execution cannot append a controlled Trial result")
    slot = next((item for item in state.trial_slots.slots if item.trial_id == trial_id), None)
    if slot is None:
        raise ContractError("cannot publish a controlled result for an unknown Trial")
    if _result_path(root, trial_id).exists():
        raise ContractError("controlled Trial result destination must not already exist")
    records = [item for item in state.active_attempts if item.trial_id == trial_id]
    if not records:
        raise ContractError("controlled Trial result requires at least one terminal Attempt")
    artifacts = {
        (trial_id, item.ordinal): _verify_attempt(_attempt_path(root, trial_id, item.ordinal))
        for item in records
    }
    chain = tuple(artifacts[(trial_id, item.ordinal)] for item in records)
    if any(item.controlled_attempt is None for item in chain):
        raise ContractError("controlled Trial result requires only V2 ControlledAttempts")
    specification = next(
        item
        for item in state.run_plan.experiment_specs
        if item.experiment_id == slot.slot.experiment_id
    )
    if not isinstance(specification, ControlledExperimentSpecV2):
        raise ContractError("controlled Trial result requires a controlled V2 plan")
    if chain[-1].terminal.retry_eligible and len(records) <= specification.retry.max_retries:
        raise ContractError("retry-eligible Trial cannot close before its frozen retry")
    selected_root = _attempt_path(root, trial_id, records[-1].ordinal)
    selected = verify_controlled_attempt_artifact(selected_root)
    staging = Path(tempfile.mkdtemp(prefix=f".{trial_id}.staging-", dir=root / "results"))
    try:
        payload: dict[str, object] = {
            "schema_version": "cernora.reference.controlled-trial-result/v1",
            "execution_id": state.record.execution_id,
            "run_plan_id": state.run_plan.run_plan_id,
            "trial_id": trial_id,
            "experiment_id": slot.slot.experiment_id,
            "selected_attempt_id": selected.attempt.attempt_id,
            "selected_attempt_artifact_id": selected.manifest.artifact_id,
            "selected_attempt_sha256": sha256_file(selected_root / "attempt.json"),
            "evaluation_status": (
                "evaluated" if selected.attempt.evaluation is not None else "unavailable"
            ),
        }
        payload["result_id"] = canonical_content_id(payload, excluded=frozenset())
        result = ControlledTrialResultManifest.model_validate(payload)
        (staging / "manifest.json").write_bytes(
            canonical_json_bytes(result.model_dump(mode="json"))
        )
        _verify_controlled_trial_result(
            staging,
            record=state.record,
            run_plan=state.run_plan,
            slot=slot,
            active_records=records,
            artifacts=artifacts,
        )
        atomic_publish_directory(staging, _result_path(root, trial_id))
        return result
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def publish_trial_manifest(root: Path, trial_id: str) -> TrialManifest:
    state = reload_execution(root)
    if state.manifest is not None:
        raise ContractError("completed Execution cannot append a Trial Manifest")
    slot = next((item for item in state.trial_slots.slots if item.trial_id == trial_id), None)
    if slot is None:
        raise ContractError("cannot complete an unknown Trial")
    records = tuple(item for item in state.active_attempts if item.trial_id == trial_id)
    if not records:
        raise ContractError("Trial cannot complete without an Attempt")
    result = next((item for item in state.trial_results if item.trial_id == trial_id), None)
    if result is None:
        raise ContractError("Trial cannot complete without an immutable Trial result")
    artifacts = tuple(
        _verify_attempt(_attempt_path(root, trial_id, item.ordinal)) for item in records
    )
    specification = next(
        item
        for item in state.run_plan.experiment_specs
        if item.experiment_id == slot.slot.experiment_id
    )
    if artifacts[-1].terminal.retry_eligible and len(records) <= specification.retry.max_retries:
        raise ContractError("retry-eligible Trial cannot complete before its frozen retry")
    selected = select_terminal_attempt(tuple(item.terminal for item in artifacts))
    bindings = tuple(
        AttemptBinding(
            ordinal=record.ordinal,
            active_record_id=record.active_record_id,
            attempt_id=artifact.terminal.attempt_id,
            predecessor_attempt_id=artifact.terminal.predecessor_attempt_id,
            source_trial_id=artifact.source_trial_id,
            artifact_kind=artifact.kind,
            artifact_manifest_sha256=artifact.manifest_sha256,
            terminal_sha256=artifact.terminal_sha256,
        )
        for record, artifact in zip(records, artifacts, strict=True)
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.trial-manifest/v1",
        "execution_id": state.record.execution_id,
        "run_plan_id": state.run_plan.run_plan_id,
        "trial_id": trial_id,
        "trial_slot_id": slot.slot.trial_slot_id,
        "experiment_id": slot.slot.experiment_id,
        "selected_attempt_id": selected.attempt_id,
        "terminal_state": selected.state,
        "result_id": result.result_id,
        "result_manifest_sha256": sha256_file(_result_path(root, trial_id) / "manifest.json"),
        "attempts": [item.model_dump(mode="json") for item in bindings],
    }
    payload["trial_manifest_id"] = canonical_content_id(payload, excluded=frozenset())
    manifest = TrialManifest.model_validate(payload)
    _publish_file(
        _trial_path(root, trial_id), canonical_json_bytes(manifest.model_dump(mode="json"))
    )
    return manifest


def publish_checkpoint(
    root: Path,
    *,
    status: Literal["running", "stopped", "budget-exhausted", "completed"] = "running",
    elapsed_milliseconds: int = 0,
) -> ExecutionCheckpoint:
    state = reload_execution(root)
    if state.manifest is not None:
        raise ContractError("completed Execution cannot append a checkpoint")
    if state.checkpoints and state.checkpoints[-1].status in {"budget-exhausted", "completed"}:
        raise ContractError("terminal checkpoint status cannot append another snapshot")
    sequence = len(state.checkpoints) + 1
    previous = (
        sha256_file(root / "checkpoints" / f"{sequence - 1:08d}.json") if sequence > 1 else None
    )
    trials = tuple(
        TrialReference(
            trial_id=item.trial_id,
            trial_manifest_sha256=sha256_file(_trial_path(root, item.trial_id)),
        )
        for item in state.trial_slots.slots
        if _trial_path(root, item.trial_id).exists()
    )
    active = tuple(
        ActiveAttemptReference(
            trial_id=item.trial_id,
            ordinal=item.ordinal,
            active_record_id=item.active_record_id,
            active_record_sha256=sha256_file(_active_path(root, item.trial_id, item.ordinal)),
        )
        for item in state.active_attempts
    )
    if state.checkpoints and elapsed_milliseconds < state.checkpoints[-1].elapsed_milliseconds:
        raise ContractError("checkpoint resource snapshots are not monotonic")
    if status == "completed" and len(trials) != len(state.trial_slots.slots):
        raise ContractError("completed checkpoint requires every planned Trial")
    attempt_budget_reached = len(active) >= state.run_plan.execution.max_attempt_count
    wall_budget_reached = elapsed_milliseconds >= (
        state.run_plan.execution.max_total_wall_time_seconds * 1000
    )
    if status == "completed" and wall_budget_reached:
        raise ContractError("completed checkpoint cannot reach the frozen wall budget")
    if status == "budget-exhausted" and not (attempt_budget_reached or wall_budget_reached):
        raise ContractError("budget-exhausted checkpoint has not reached a frozen budget")
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.execution-checkpoint/v1",
        "execution_id": state.record.execution_id,
        "run_plan_id": state.run_plan.run_plan_id,
        "sequence": sequence,
        "previous_checkpoint_sha256": previous,
        "status": status,
        "elapsed_milliseconds": elapsed_milliseconds,
        "attempt_count": len(active),
        "completed_trials": [item.model_dump(mode="json") for item in trials],
        "active_attempts": [item.model_dump(mode="json") for item in active],
    }
    payload["checkpoint_id"] = canonical_content_id(payload, excluded=frozenset())
    checkpoint = ExecutionCheckpoint.model_validate(payload)
    _publish_file(
        root / "checkpoints" / f"{sequence:08d}.json",
        canonical_json_bytes(checkpoint.model_dump(mode="json")),
    )
    return checkpoint


def publish_execution_manifest(root: Path) -> ExecutionManifest:
    state = reload_execution(root)
    if len(state.trial_manifests) != len(state.trial_slots.slots):
        raise ContractError("Execution Manifest requires every planned Trial exactly once")
    if not state.checkpoints:
        raise ContractError("Execution Manifest requires a complete checkpoint")
    latest = state.checkpoints[-1]
    if latest.status != "completed":
        raise ContractError("Execution Manifest requires a completed checkpoint")
    if {item.trial_id for item in latest.completed_trials} != {
        item.trial_id for item in state.trial_slots.slots
    }:
        raise ContractError("latest checkpoint does not contain every planned Trial")
    results = {item.trial_id: item for item in state.trial_results}
    if set(results) != {item.trial_id for item in state.trial_slots.slots}:
        raise ContractError("Execution Manifest requires every immutable Trial result")
    diagnostic = _build_diagnostic(
        state.record,
        state.run_plan,
        state.trial_slots,
        state.trial_manifests,
        results,
    )
    diagnostic_root = root / "diagnostic"
    if state.diagnostic is None:
        staging = Path(tempfile.mkdtemp(prefix=".diagnostic.staging-", dir=root))
        try:
            (staging / "diagnostic.json").write_bytes(
                canonical_json_bytes(diagnostic.model_dump(mode="json"))
            )
            (staging / "diagnostic.md").write_text(
                diagnostic.markdown(), encoding="utf-8", newline=""
            )
            atomic_publish_directory(staging, diagnostic_root)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
    elif state.diagnostic != diagnostic:
        raise ContractError("preexisting Execution diagnostic does not match lifecycle facts")
    trials = tuple(
        TrialReference(
            trial_id=item.trial_id,
            trial_manifest_sha256=sha256_file(_trial_path(root, item.trial_id)),
        )
        for item in state.trial_slots.slots
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.execution-manifest/v1",
        "execution_id": state.record.execution_id,
        "run_plan_id": state.run_plan.run_plan_id,
        "run_plan_sha256": state.record.run_plan_sha256,
        "trial_slots_sha256": state.record.trial_slots_sha256,
        "latest_checkpoint_sha256": sha256_file(
            root / "checkpoints" / f"{latest.sequence:08d}.json"
        ),
        "diagnostic_json_sha256": sha256_file(diagnostic_root / "diagnostic.json"),
        "diagnostic_markdown_sha256": sha256_file(diagnostic_root / "diagnostic.md"),
        "trials": [item.model_dump(mode="json") for item in trials],
    }
    payload["execution_manifest_id"] = canonical_content_id(payload, excluded=frozenset())
    manifest = ExecutionManifest.model_validate(payload)
    _publish_file(
        root / "execution-manifest.json", canonical_json_bytes(manifest.model_dump(mode="json"))
    )
    reload_execution(root)
    return manifest


def _closed_file_entries(root: Path, *, prefix: str = "") -> tuple[ClosedFile, ...]:
    return tuple(
        ClosedFile(
            path=f"{prefix}{relative}",
            byte_length=path.stat().st_size,
            sha256=sha256_file(path),
        )
        for relative, path in closed_regular_tree(root).items()
    )


def verify_execution_pack(root: Path) -> ExecutionPackManifest:
    files = closed_regular_tree(root)
    if "manifest.json" not in files:
        raise ContractError("Execution Pack is missing manifest.json")
    manifest = _load_canonical(files["manifest.json"], ExecutionPackManifest)
    indexed = {item.path: item for item in manifest.files}
    observed = set(files) - {"manifest.json"}
    if set(indexed) != observed:
        raise ContractError("Execution Pack manifest does not close its file tree")
    for relative, entry in indexed.items():
        path = files[relative]
        if path.stat().st_size != entry.byte_length or sha256_file(path) != entry.sha256:
            raise ContractError("Execution Pack file digest mismatch")
    state = reload_execution(root / "execution")
    if (
        state.manifest is None
        or manifest.execution_id != state.record.execution_id
        or manifest.run_plan_id != state.run_plan.run_plan_id
    ):
        raise ContractError("Execution Pack does not contain one complete bound Execution")
    return manifest


def publish_execution_pack(execution_root: Path, destination: Path) -> ExecutionPackManifest:
    state = reload_execution(execution_root)
    if state.manifest is None:
        raise ContractError("Execution Pack requires a completed Execution")
    if not destination.parent.is_dir() or destination.exists() or destination.is_symlink():
        raise ContractError("Execution Pack destination parent must exist and destination must not")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        shutil.copytree(execution_root, staging / "execution")
        entries = _closed_file_entries(staging / "execution", prefix="execution/")
        payload: dict[str, object] = {
            "schema_version": "cernora.reference.execution-pack/v1",
            "execution_id": state.record.execution_id,
            "run_plan_id": state.run_plan.run_plan_id,
            "files": [item.model_dump(mode="json") for item in entries],
        }
        payload["pack_id"] = canonical_content_id(payload, excluded=frozenset())
        manifest = ExecutionPackManifest.model_validate(payload)
        (staging / "manifest.json").write_bytes(
            canonical_json_bytes(manifest.model_dump(mode="json"))
        )
        verify_execution_pack(staging)
        atomic_publish_directory(staging, destination)
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def rebuild_execution_pack(pack_root: Path, destination: Path) -> ExecutionManifest:
    """Rebuild outputs using filesystem reads and canonical rendering only."""

    verify_execution_pack(pack_root)
    if not destination.parent.is_dir() or destination.exists() or destination.is_symlink():
        raise ContractError("rebuild destination parent must exist and destination must not")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    shutil.rmtree(staging)
    try:
        shutil.copytree(pack_root / "execution", staging)
        shutil.rmtree(staging / "diagnostic")
        (staging / "execution-manifest.json").unlink()
        rebuilt = publish_execution_manifest(staging)
        reload_execution(staging)
        packed_files = closed_regular_tree(pack_root / "execution")
        rebuilt_files = closed_regular_tree(staging)
        if set(packed_files) != set(rebuilt_files) or any(
            packed_files[name].read_bytes() != rebuilt_files[name].read_bytes()
            for name in packed_files
        ):
            raise ContractError("offline rebuild is not byte-identical to the packed Execution")
        atomic_publish_directory(staging, destination)
        return rebuilt
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


__all__ = [
    "ActiveAttemptRecord",
    "AttemptBinding",
    "ControlledTrialResultManifest",
    "ExecutionCheckpoint",
    "ExecutionManifest",
    "ExecutionPackManifest",
    "ExecutionRecord",
    "ExecutionState",
    "ExecutionTrialSlot",
    "ExecutionTrialSlots",
    "TrialManifest",
    "TrialResultManifest",
    "initialize_execution",
    "publish_checkpoint",
    "publish_controlled_trial_result",
    "publish_execution_manifest",
    "publish_execution_pack",
    "publish_trial_manifest",
    "publish_trial_result",
    "rebuild_execution_pack",
    "reload_execution",
    "reload_execution_for_reconciliation",
    "start_attempt",
    "verify_execution_pack",
]
