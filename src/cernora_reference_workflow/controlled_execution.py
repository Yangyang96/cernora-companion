"""Strict in-memory execution records for controlled RunPlan v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, Self

from cernora import BatchAttemptResources, BatchEvaluationPackage, BatchLifecycleRecord
from pydantic import Field, field_validator, model_validator

from cernora_reference_workflow.common import canonical_content_id
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
        if self.lifecycle is not None and self.retry_eligible:
            eligible = self.lifecycle.category.replace("_", "-")
            if eligible not in spec.retry.eligible_states:
                raise ValueError("Attempt retries a lifecycle outside the frozen retry policy")


def materialize_controlled_attempt(
    payload_without_identity: dict[str, object],
) -> ControlledAttempt:
    if "attempt_id" in payload_without_identity:
        raise ValueError("controlled Attempt materialization input must omit attempt_id")
    payload = dict(payload_without_identity)
    payload["attempt_id"] = canonical_content_id(payload, excluded=frozenset())
    return ControlledAttempt.model_validate(payload)


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
    "ControlledAttemptExecutor",
    "ControlledAttemptRequest",
    "ControlledExecutionResult",
    "ControlledTrialExecution",
    "materialize_controlled_attempt",
]
