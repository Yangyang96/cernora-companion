"""Strict Priority 4 RunPlan v1 and deterministic Trial-slot expansion."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    load_json_file,
)
from cernora_reference_workflow.experiment_spec import Digest, ExperimentSpec, StrictContract

Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
PositiveInt = Annotated[StrictInt, Field(gt=0)]


class RunPlanCase(StrictContract):
    case_id: Identifier
    case_version: Annotated[StrictStr, Field(min_length=1)]
    task_content_sha256: Digest


class RunPlanConfiguration(StrictContract):
    configuration_id: Identifier


class RunPlanCell(StrictContract):
    case_id: Identifier
    configuration_id: Identifier
    experiment_id: Digest


class ConnectorIdentity(StrictContract):
    connector_id: Literal["cernora-reference-harbor-codex"]
    connector_version: Literal["1"]
    platform_qualification: Literal["macos-arm64"]


class UnavailableBudgetControl(StrictContract):
    status: Literal["unavailable"]
    reason: Literal["no-structured-authoritative-source"]


class RunExecutionPolicy(StrictContract):
    concurrency: Literal[1]
    max_attempt_count: PositiveInt
    max_total_wall_time_seconds: PositiveInt
    token_budget: UnavailableBudgetControl
    monetary_budget: UnavailableBudgetControl


class RunAnalysisPolicy(StrictContract):
    method: Literal["none"]
    method_version: Literal["m1"]
    aggregate_quality_conclusion: Literal[False]


class TrialSlot(StrictContract):
    schema_version: Literal["cernora.reference.trial-slot/v1"]
    trial_slot_id: Digest
    run_plan_id: Digest
    slot_index: PositiveInt
    case_id: Identifier
    configuration_id: Identifier
    experiment_id: Digest
    repetition: PositiveInt


class RunPlan(StrictContract):
    schema_version: Literal["cernora.reference.run-plan/v1"]
    run_plan_id: Digest
    companion_version: Literal["0.2.0"]
    cernora_version: Literal["0.1.2"]
    connector: ConnectorIdentity
    experiment_specs: tuple[ExperimentSpec, ...] = Field(min_length=1)
    cases: tuple[RunPlanCase, ...] = Field(min_length=1)
    configurations: tuple[RunPlanConfiguration, ...] = Field(min_length=1)
    cells: tuple[RunPlanCell, ...] = Field(min_length=1)
    repetitions: PositiveInt
    pairing_rule: Literal["case-configuration-repetition"]
    planned_trial_count: PositiveInt
    worst_case_attempt_count: PositiveInt
    execution: RunExecutionPolicy
    analysis: RunAnalysisPolicy

    @model_validator(mode="after")
    def validate_matrix_identity_and_budgets(self) -> RunPlan:
        case_ids = tuple(item.case_id for item in self.cases)
        configuration_ids = tuple(item.configuration_id for item in self.configurations)
        experiment_ids = tuple(item.experiment_id for item in self.experiment_specs)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("RunPlan case IDs must be unique")
        if len(configuration_ids) != len(set(configuration_ids)):
            raise ValueError("RunPlan configuration IDs must be unique")
        if len(experiment_ids) != len(set(experiment_ids)):
            raise ValueError("RunPlan must embed each ExperimentSpec exactly once")

        cases = {item.case_id: item for item in self.cases}
        specifications = {item.experiment_id: item for item in self.experiment_specs}
        pairs: set[tuple[str, str]] = set()
        referenced_experiments: set[str] = set()
        referenced_cases: set[str] = set()
        referenced_configurations: set[str] = set()
        worst_case_attempt_count = 0
        for cell in self.cells:
            pair = (cell.case_id, cell.configuration_id)
            if pair in pairs:
                raise ValueError("RunPlan cells must bind each Case and Configuration at most once")
            pairs.add(pair)
            if cell.case_id not in cases:
                raise ValueError("RunPlan cell references an unknown Case")
            if cell.configuration_id not in configuration_ids:
                raise ValueError("RunPlan cell references an unknown Configuration")
            spec = specifications.get(cell.experiment_id)
            if spec is None:
                raise ValueError("RunPlan cell references an unknown ExperimentSpec")
            case = cases[cell.case_id]
            if (
                spec.task.task_id != case.case_id
                or spec.task.task_version != case.case_version
                or spec.task.content_sha256 != case.task_content_sha256
            ):
                raise ValueError("RunPlan cell ExperimentSpec does not bind its declared Case")
            referenced_experiments.add(cell.experiment_id)
            referenced_cases.add(cell.case_id)
            referenced_configurations.add(cell.configuration_id)
            worst_case_attempt_count += self.repetitions * (1 + spec.retry.max_retries)

        if referenced_experiments != set(experiment_ids):
            raise ValueError("RunPlan contains an unreferenced ExperimentSpec")
        if referenced_cases != set(case_ids):
            raise ValueError("RunPlan contains an unreferenced Case")
        if referenced_configurations != set(configuration_ids):
            raise ValueError("RunPlan contains an unreferenced Configuration")
        planned_trial_count = len(self.cells) * self.repetitions
        if self.planned_trial_count != planned_trial_count:
            raise ValueError("planned_trial_count does not match the frozen matrix")
        if self.worst_case_attempt_count != worst_case_attempt_count:
            raise ValueError("worst_case_attempt_count does not match the frozen retry policies")
        if not planned_trial_count <= self.execution.max_attempt_count <= worst_case_attempt_count:
            raise ValueError(
                "max_attempt_count must cover every Trial without exceeding retry scope"
            )
        if self.run_plan_id != self.compute_run_plan_id(self.model_dump(mode="json")):
            raise ValueError("run_plan_id does not match canonical RunPlan content")
        return self

    @staticmethod
    def compute_run_plan_id(payload: dict[str, object]) -> str:
        return canonical_content_id(payload, excluded=frozenset({"run_plan_id"}))

    @classmethod
    def from_file(cls, path: Path) -> RunPlan:
        payload = load_json_file(path)
        if not isinstance(payload, dict):
            raise ContractError("RunPlan must be a JSON object")
        plan = cls.model_validate(payload)
        if path.read_bytes() != plan.canonical_bytes():
            raise ContractError("RunPlan is not canonical JSON")
        return plan

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def expand_trial_slots(self) -> tuple[TrialSlot, ...]:
        slots: list[TrialSlot] = []
        for cell in self.cells:
            for repetition in range(1, self.repetitions + 1):
                identity = {
                    "case_id": cell.case_id,
                    "configuration_id": cell.configuration_id,
                    "experiment_id": cell.experiment_id,
                    "repetition": repetition,
                    "run_plan_id": self.run_plan_id,
                }
                slots.append(
                    TrialSlot(
                        schema_version="cernora.reference.trial-slot/v1",
                        trial_slot_id=canonical_content_id(identity, excluded=frozenset()),
                        run_plan_id=self.run_plan_id,
                        slot_index=len(slots) + 1,
                        case_id=cell.case_id,
                        configuration_id=cell.configuration_id,
                        experiment_id=cell.experiment_id,
                        repetition=repetition,
                    )
                )
        return tuple(slots)


def materialize_run_plan(payload_without_id: dict[str, object]) -> RunPlan:
    if "run_plan_id" in payload_without_id:
        raise ContractError("RunPlan materialization input must omit run_plan_id")
    payload = dict(payload_without_id)
    payload["run_plan_id"] = RunPlan.compute_run_plan_id(payload)
    return RunPlan.model_validate(payload)


__all__ = [
    "ConnectorIdentity",
    "RunAnalysisPolicy",
    "RunExecutionPolicy",
    "RunPlan",
    "RunPlanCase",
    "RunPlanCell",
    "RunPlanConfiguration",
    "TrialSlot",
    "UnavailableBudgetControl",
    "materialize_run_plan",
]
