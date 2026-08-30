"""Strict in-memory execution records for controlled RunPlan v2."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from cernora import BatchAttemptResources, BatchEvaluationPackage, BatchLifecycleRecord
from pydantic import Field, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_file,
)
from cernora_reference_workflow.controlled_evaluation import RepairResultRecord
from cernora_reference_workflow.controlled_experiment_spec import (
    ControlledExperimentSpecV2,
    Digest,
    StrictV2Contract,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    ControlledTrialSlotV2,
)
from cernora_reference_workflow.controlled_runtime import RuntimeAuthorityObservation
from cernora_reference_workflow.lifecycle import TerminalRecord, TerminalState
from cernora_reference_workflow.publication import atomic_publish_directory


class ControlledAttempt(StrictV2Contract):
    """One immutable Attempt; retries remain inside its Trial."""

    schema_version: Literal["cernora.reference.controlled-attempt/v1"]
    attempt_id: Digest
    trial_id: Digest
    ordinal: Annotated[int, Field(gt=0)]
    predecessor_attempt_id: Digest | None
    source_attempt_id: Digest
    source_manifest_sha256: Digest
    retry_eligible: bool
    resources: BatchAttemptResources
    runtime_observation: RuntimeAuthorityObservation | None = None
    repair_result: RepairResultRecord | None = None
    evaluation: BatchEvaluationPackage | None = None
    lifecycle: BatchLifecycleRecord | None = None

    @model_validator(mode="after")
    def coherent_terminal_and_identity(self) -> Self:
        evaluated = (
            self.runtime_observation is not None,
            self.repair_result is not None,
            self.evaluation is not None,
        )
        if self.lifecycle is None:
            if not all(evaluated):
                raise ValueError("evaluated Attempt requires observation, result, and package")
            if self.retry_eligible:
                raise ValueError("evaluated behavioral outcomes are never retry eligible")
            assert self.repair_result is not None
            if not self.repair_result.evaluation_valid:
                raise ValueError("evaluated Attempt requires a valid repair evaluation")
            self._verify_evaluation_evidence()
        elif any(evaluated):
            raise ValueError("infrastructure Attempt cannot carry evaluated evidence")
        elif self.retry_eligible != self.lifecycle.retry_eligible:
            raise ValueError("Attempt retry eligibility contradicts lifecycle receipt")
        if self.ordinal == 1 and self.predecessor_attempt_id is not None:
            raise ValueError("first Attempt cannot have a predecessor")
        if self.ordinal > 1 and self.predecessor_attempt_id is None:
            raise ValueError("retry Attempt requires a predecessor")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"attempt_id"})
        )
        if self.attempt_id != expected:
            raise ValueError("controlled Attempt identity mismatch")
        return self

    def verify_authority(self, spec: ControlledExperimentSpecV2) -> None:
        if self.runtime_observation is not None:
            self.runtime_observation.verify(spec)
        if self.repair_result is not None and (
            self.repair_result.case_id != spec.task.task_id
            or self.repair_result.test_authority_sha256 != spec.test_runner.authority_sha256
            or self.repair_result.test_plan_sha256 != spec.test_runner.test_plan_sha256
            or self.repair_result.test_source_sha256 != spec.test_runner.test_source_sha256
            or self.repair_result.allowed_paths != spec.task.allowed_paths
            or self.repair_result.protected_paths != spec.task.protected_paths
        ):
            raise ValueError("repair result does not bind the selected Experiment authority")
        if self.evaluation is not None:
            receipt, _ = _evaluation_documents(self.evaluation)
            if receipt.get("authority") != spec.expected_evaluation_authority.model_dump(
                mode="json"
            ):
                raise ValueError("Evaluation authority does not bind the selected Experiment")
            authority = receipt.get("authority")
            if not isinstance(authority, dict):
                raise ValueError("Evaluation receipt authority must be a JSON object")
            policy_payload = {
                "schema_version": "agent.evaluator.comparison-evaluation-policy/v1",
                "profile": receipt.get("profile"),
                "projection": authority.get("projection"),
                "scorer": receipt.get("scorer"),
                "case_gate": receipt.get("case_gate"),
            }
            if (
                canonical_content_id(policy_payload, excluded=frozenset())
                != spec.expected_evaluation_policy.policy_sha256
            ):
                raise ValueError("Evaluation policy does not bind the selected Experiment")
        if self.lifecycle is not None and self.retry_eligible:
            eligible = self.lifecycle.category.replace("_", "-")
            if eligible not in spec.retry.eligible_states:
                raise ValueError("Attempt retries a lifecycle outside the frozen retry policy")

    def _verify_evaluation_evidence(self) -> None:
        assert self.evaluation is not None
        assert self.repair_result is not None
        receipt, evidence = _evaluation_documents(self.evaluation)
        run = receipt.get("run")
        if not isinstance(run, dict) or run.get("attempt_id") != self.source_attempt_id:
            raise ValueError("Evaluation Package does not bind the source Attempt")
        metadata = evidence.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("result_id") != (
            self.repair_result.result_id
        ):
            raise ValueError("Evaluation Package does not bind the repair result")
        case = receipt.get("case")
        if not isinstance(case, dict) or case.get("case_id") != self.repair_result.case_id:
            raise ValueError("Evaluation Package does not bind the repair Case")
        expected_outcome = "pass" if self.repair_result.passed else "fail"
        if receipt.get("case_outcome") != expected_outcome:
            raise ValueError("Evaluation outcome contradicts the repair result")


def _evaluation_documents(
    package: BatchEvaluationPackage,
) -> tuple[dict[str, object], dict[str, object]]:
    files = package.file_payloads()
    documents: list[dict[str, object]] = []
    for name in ("evaluation-receipt.json", "evidence.json"):
        raw = files.get(name)
        if raw is None:
            raise ValueError(f"Evaluation Package is missing {name}")
        payload = load_json_bytes(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"Evaluation Package {name} must contain one JSON object")
        documents.append(payload)
    return documents[0], documents[1]


def materialize_controlled_attempt(
    payload_without_identity: dict[str, object],
) -> ControlledAttempt:
    if "attempt_id" in payload_without_identity:
        raise ValueError("controlled Attempt materialization input must omit attempt_id")
    payload = dict(payload_without_identity)
    payload["attempt_id"] = canonical_content_id(payload, excluded=frozenset())
    return ControlledAttempt.model_validate(payload)


class ControlledAttemptArtifactManifest(StrictV2Contract):
    """Closed on-disk binding for one V2 Attempt adopted by the Repeat Runner."""

    schema_version: Literal["cernora.reference.controlled-attempt-artifact/v1"]
    artifact_id: Digest
    experiment_id: Digest
    trial_id: Digest
    attempt_id: Digest
    attempt_sha256: Digest
    terminal_sha256: Digest

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"artifact_id"})
        )
        if self.artifact_id != expected:
            raise ValueError("controlled Attempt artifact identity mismatch")
        return self


@dataclass(frozen=True)
class VerifiedControlledAttemptArtifact:
    manifest: ControlledAttemptArtifactManifest
    attempt: ControlledAttempt
    terminal: TerminalRecord


def _controlled_terminal(attempt: ControlledAttempt) -> TerminalRecord:
    lifecycle = attempt.lifecycle
    if lifecycle is None:
        repair_result = attempt.repair_result
        if repair_result is None:
            raise ContractError("evaluated controlled Attempt is missing its repair result")
        passed = repair_result.passed
        return TerminalRecord(
            schema_version="cernora.reference.terminal/v1",
            attempt_id=attempt.attempt_id,
            state="completed" if passed else "behavioral-failure",
            reason="authoritative-repair-passed" if passed else "authoritative-repair-failed",
            retry_eligible=False,
            predecessor_attempt_id=attempt.predecessor_attempt_id,
        )
    states: dict[str, TerminalState] = {
        "timed_out": "timed-out",
        "interrupted": "interrupted",
        "infrastructure_start_failure": "infrastructure-start-failure",
        "transient_provider_pre_terminal": "transient-provider-pre-terminal",
        "runtime_pre_terminal_failure": "runtime-pre-terminal-failure",
        "other_verified_infrastructure_failure": "runtime-pre-terminal-failure",
    }
    return TerminalRecord(
        schema_version="cernora.reference.terminal/v1",
        attempt_id=attempt.attempt_id,
        state=states[lifecycle.category],
        reason=lifecycle.source_state,
        retry_eligible=attempt.retry_eligible,
        predecessor_attempt_id=attempt.predecessor_attempt_id,
    )


def verify_controlled_attempt_artifact(root: Path) -> VerifiedControlledAttemptArtifact:
    """Strictly reload one closed V2 Attempt artifact without Runtime access."""

    files = closed_regular_tree(root)
    if set(files) != {"attempt.json", "manifest.json", "terminal.json"}:
        raise ContractError("controlled Attempt artifact has an invalid closed file set")
    parsed: list[object] = []
    for name, model in (
        ("manifest.json", ControlledAttemptArtifactManifest),
        ("attempt.json", ControlledAttempt),
        ("terminal.json", TerminalRecord),
    ):
        raw = read_regular_file_bytes(files[name])
        payload = load_json_bytes(raw)
        if not isinstance(payload, dict):
            raise ContractError(f"controlled Attempt {name} must contain one JSON object")
        value = model.model_validate_json(raw)
        if raw != canonical_json_bytes(value.model_dump(mode="json")):
            raise ContractError(f"controlled Attempt {name} is not canonical JSON")
        parsed.append(value)
    manifest, attempt, terminal = parsed
    assert isinstance(manifest, ControlledAttemptArtifactManifest)
    assert isinstance(attempt, ControlledAttempt)
    assert isinstance(terminal, TerminalRecord)
    if (
        manifest.trial_id != attempt.trial_id
        or manifest.attempt_id != attempt.attempt_id
        or terminal.attempt_id != attempt.attempt_id
        or terminal.predecessor_attempt_id != attempt.predecessor_attempt_id
        or terminal.retry_eligible != attempt.retry_eligible
        or manifest.attempt_sha256 != sha256_file(files["attempt.json"])
        or manifest.terminal_sha256 != sha256_file(files["terminal.json"])
    ):
        raise ContractError("controlled Attempt artifact bindings do not match")
    return VerifiedControlledAttemptArtifact(
        manifest=manifest,
        attempt=attempt,
        terminal=terminal,
    )


def publish_controlled_attempt_artifact(
    destination: Path,
    *,
    attempt: ControlledAttempt,
    specification: ControlledExperimentSpecV2,
) -> VerifiedControlledAttemptArtifact:
    """Atomically publish one authority-verified V2 Attempt into assigned custody."""

    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ContractError("controlled Attempt destination must be one new child")
    attempt.verify_authority(specification)
    terminal = _controlled_terminal(attempt)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    published = False
    try:
        (staging / "attempt.json").write_bytes(
            canonical_json_bytes(attempt.model_dump(mode="json"))
        )
        (staging / "terminal.json").write_bytes(
            canonical_json_bytes(terminal.model_dump(mode="json"))
        )
        payload: dict[str, object] = {
            "schema_version": "cernora.reference.controlled-attempt-artifact/v1",
            "experiment_id": specification.experiment_id,
            "trial_id": attempt.trial_id,
            "attempt_id": attempt.attempt_id,
            "attempt_sha256": sha256_file(staging / "attempt.json"),
            "terminal_sha256": sha256_file(staging / "terminal.json"),
        }
        payload["artifact_id"] = canonical_content_id(payload, excluded=frozenset())
        manifest = ControlledAttemptArtifactManifest.model_validate(payload)
        (staging / "manifest.json").write_bytes(
            canonical_json_bytes(manifest.model_dump(mode="json"))
        )
        verified = verify_controlled_attempt_artifact(staging)
        atomic_publish_directory(staging, destination)
        published = True
        return verified
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


class ControlledTrialExecution(StrictV2Contract):
    schema_version: Literal["cernora.reference.controlled-trial-execution/v1"]
    trial_id: Digest
    slot: ControlledTrialSlotV2
    attempts: Annotated[tuple[ControlledAttempt, ...], Field(min_length=1, max_length=2)]
    selected_attempt_id: Digest

    @field_validator("attempts", mode="before")
    @classmethod
    def tuple_attempts(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def valid_chain(self) -> Self:
        if tuple(item.ordinal for item in self.attempts) != tuple(range(1, len(self.attempts) + 1)):
            raise ValueError("Trial Attempt ordinals must be contiguous")
        if any(item.trial_id != self.trial_id for item in self.attempts):
            raise ValueError("Trial contains an Attempt for another Trial")
        for previous, current in zip(self.attempts, self.attempts[1:], strict=False):
            if current.predecessor_attempt_id != previous.attempt_id or not previous.retry_eligible:
                raise ValueError("Trial retry chain is invalid")
        if self.selected_attempt_id != self.attempts[-1].attempt_id:
            raise ValueError("Trial must select its final Attempt")
        if self.attempts[-1].retry_eligible and len(self.attempts) == 1:
            raise ValueError("retry-eligible Trial is not complete")
        return self


class ControlledExecutionResult(StrictV2Contract):
    """One complete 2 x Cases x 3 controlled execution."""

    schema_version: Literal["cernora.reference.controlled-execution-result/v1"]
    execution_id: Digest
    run_plan_id: Digest
    nonce: Digest
    status: Literal["completed"]
    budget_status: Literal["within_budget"]
    trials: Annotated[tuple[ControlledTrialExecution, ...], Field(min_length=1)]
    attempt_count: Annotated[int, Field(gt=0)]

    @field_validator("trials", mode="before")
    @classmethod
    def tuple_trials(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def canonical_identity_and_counts(self) -> Self:
        if self.attempt_count != sum(len(item.attempts) for item in self.trials):
            raise ValueError("controlled execution Attempt count mismatch")
        if len({item.trial_id for item in self.trials}) != len(self.trials):
            raise ValueError("controlled execution Trials must be unique")
        expected = canonical_content_id(
            {"nonce": self.nonce, "run_plan_id": self.run_plan_id}, excluded=frozenset()
        )
        if self.execution_id != expected:
            raise ValueError("controlled execution identity mismatch")
        return self

    def verify_plan(self, plan: ControlledRunPlanV2) -> None:
        if self.run_plan_id != plan.run_plan_id:
            raise ValueError("controlled execution binds another RunPlan")
        slots = plan.expand_trial_slots()
        if tuple(item.slot for item in self.trials) != slots:
            raise ValueError("controlled execution does not contain the complete Trial matrix")
        if self.attempt_count > plan.execution.max_attempt_count:
            raise ValueError("controlled execution exceeds the Attempt budget")
        specs = {(item.task.task_id, item.configuration_id): item for item in plan.experiment_specs}
        for trial in self.trials:
            spec = specs[(trial.slot.case_id, trial.slot.configuration_id)]
            for attempt in trial.attempts:
                attempt.verify_authority(spec)


@dataclass(frozen=True)
class ControlledAttemptRequest:
    trial_id: str
    slot: ControlledTrialSlotV2
    specification: ControlledExperimentSpecV2
    ordinal: int
    predecessor_attempt_id: str | None
    global_deadline_monotonic: float


class ControlledAttemptExecutor(Protocol):
    """Executor that actively terminates work at the supplied global deadline."""

    @property
    def enforces_hard_deadline(self) -> bool: ...

    def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt: ...


__all__ = [
    "ControlledAttempt",
    "ControlledAttemptArtifactManifest",
    "ControlledAttemptExecutor",
    "ControlledAttemptRequest",
    "ControlledExecutionResult",
    "ControlledTrialExecution",
    "VerifiedControlledAttemptArtifact",
    "materialize_controlled_attempt",
    "publish_controlled_attempt_artifact",
    "verify_controlled_attempt_artifact",
]
