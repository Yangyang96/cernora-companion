"""Strict controlled RunPlan v2 with authority-bound Trial slots."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    load_json_bytes,
    read_regular_file_bytes,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    ControlledExperimentSpecV2,
    Digest,
    Identifier,
    PositiveInt,
    StrictV2Contract,
)
from cernora_reference_workflow.run_plan import (
    ConnectorIdentity,
    RunExecutionPolicy,
    RunPlanCase,
    RunPlanCell,
    RunPlanConfiguration,
)


class ControlledRunAnalysisPolicy(StrictV2Contract):
    method: Literal["controlled-comparison"]
    method_version: Literal["m3", "m4"]
    aggregate_quality_conclusion: Literal[False]


class ControlledTrialSlotV2(StrictV2Contract):
    schema_version: Literal["cernora.reference.controlled-trial-slot/v2"]
    trial_slot_id: Digest
    run_plan_id: Digest
    slot_index: PositiveInt
    case_id: Identifier
    configuration_id: Identifier
    experiment_id: Digest
    repetition: PositiveInt


class ControlledRunPlanV2(StrictV2Contract):
    schema_version: Literal["cernora.reference.controlled-run-plan/v2"]
    run_plan_id: Digest
    companion_version: Literal["0.3.0", "0.4.0", "0.4.2"]
    cernora_version: Literal["0.1.4"]
    connector: ConnectorIdentity
    experiment_specs: tuple[ControlledExperimentSpecV2, ...] = Field(min_length=2)
    cases: tuple[RunPlanCase, ...] = Field(min_length=1)
    configurations: tuple[RunPlanConfiguration, ...] = Field(min_length=2)
    cells: tuple[RunPlanCell, ...] = Field(min_length=2)
    repetitions: PositiveInt
    pairing_rule: Literal["case-configuration-repetition"]
    planned_trial_count: PositiveInt
    worst_case_attempt_count: PositiveInt
    execution: RunExecutionPolicy
    analysis: ControlledRunAnalysisPolicy

    @field_validator("experiment_specs", "cases", "configurations", "cells", mode="before")
    @classmethod
    def tuple_collections(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_matrix_identity_and_budgets(self) -> Self:
        case_ids = tuple(item.case_id for item in self.cases)
        configuration_ids = tuple(item.configuration_id for item in self.configurations)
        spec_coordinates = tuple(
            (item.task.task_id, item.configuration_id) for item in self.experiment_specs
        )
        cell_coordinates = tuple((item.case_id, item.configuration_id) for item in self.cells)
        if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("ControlledRunPlan Cases must be sorted and unique")
        if configuration_ids != tuple(sorted(configuration_ids)) or len(configuration_ids) != len(
            set(configuration_ids)
        ):
            raise ValueError("ControlledRunPlan Configurations must be sorted and unique")
        if len(configuration_ids) != 2:
            raise ValueError("controlled comparison requires exactly two Configurations")
        if spec_coordinates != tuple(sorted(spec_coordinates)) or len(spec_coordinates) != len(
            set(spec_coordinates)
        ):
            raise ValueError("controlled ExperimentSpecs must be sorted and unique by cell")
        if cell_coordinates != tuple(sorted(cell_coordinates)) or len(cell_coordinates) != len(
            set(cell_coordinates)
        ):
            raise ValueError("controlled RunPlan cells must be sorted and unique")

        expected_coordinates = {
            (case_id, configuration_id)
            for case_id in case_ids
            for configuration_id in configuration_ids
        }
        if set(spec_coordinates) != expected_coordinates or set(cell_coordinates) != (
            expected_coordinates
        ):
            raise ValueError("ControlledRunPlan must contain the exact complete comparison matrix")

        cases = {item.case_id: item for item in self.cases}
        specifications = {
            (item.task.task_id, item.configuration_id): item for item in self.experiment_specs
        }
        for cell in self.cells:
            case = cases[cell.case_id]
            spec = specifications[(cell.case_id, cell.configuration_id)]
            if (
                spec.task.task_version != case.case_version
                or spec.task.content_sha256 != case.task_content_sha256
            ):
                raise ValueError("controlled cell ExperimentSpec does not bind its declared Case")
            if cell.experiment_id != spec.experiment_id or (
                cell.experiment_id != spec.core_authority().experiment_id
            ):
                raise ValueError("controlled cell must use the Core Experiment authority ID")

        for configuration_id in configuration_ids:
            projections = {
                tuple(
                    sorted(
                        spec.core_projection()
                        .model_dump(mode="json", exclude={"evaluation_authority_sha256"})
                        .items()
                    )
                )
                for spec in self.experiment_specs
                if spec.configuration_id == configuration_id
            }
            if len(projections) != 1:
                raise ValueError("configuration-global Experiment projection differs across Cases")

        planned_trial_count = len(self.cells) * self.repetitions
        worst_case_attempt_count = sum(
            self.repetitions * (1 + spec.retry.max_retries) for spec in self.experiment_specs
        )
        if self.planned_trial_count != planned_trial_count:
            raise ValueError("planned_trial_count does not match the exact matrix")
        if self.worst_case_attempt_count != worst_case_attempt_count:
            raise ValueError("worst_case_attempt_count does not match exact retry scope")
        if self.execution.concurrency != 1:
            raise ValueError("controlled execution concurrency must be one")
        if self.execution.max_attempt_count != worst_case_attempt_count:
            raise ValueError("controlled max_attempt_count must equal worst-case Attempts")
        if self.companion_version == "0.3.0":
            if self.analysis.method_version != "m3":
                raise ValueError("Companion 0.3.0 requires the accepted M3 analysis boundary")
        elif self.companion_version == "0.4.0":
            if (
                self.analysis.method_version != "m4"
                or configuration_ids != ("baseline", "candidate")
                or len(self.cases) != 9
                or self.repetitions != 3
                or self.planned_trial_count != 54
                or self.worst_case_attempt_count != 108
                or self.execution.max_attempt_count != 108
                or self.execution.max_total_wall_time_seconds != 43_200
            ):
                raise ValueError("Companion 0.4.0 requires the exact frozen M4 execution matrix")
        else:
            if (
                self.analysis.method_version != "m4"
                or configuration_ids != ("baseline", "candidate")
                or len(self.cases) != 12
                or self.repetitions != 3
                or self.planned_trial_count != 72
                or self.worst_case_attempt_count != 144
                or self.execution.max_attempt_count != 144
                or self.execution.max_total_wall_time_seconds != 160_000
            ):
                raise ValueError(
                    "Companion 0.4.2 requires the exact frozen P4 study execution matrix"
                )
        if self.run_plan_id != self.compute_run_plan_id(self.model_dump(mode="json")):
            raise ValueError("run_plan_id does not match canonical ControlledRunPlan content")
        return self

    @staticmethod
    def compute_run_plan_id(payload: Mapping[str, object]) -> str:
        return canonical_content_id(payload, excluded=frozenset({"run_plan_id"}))

    @classmethod
    def from_bytes(cls, data: bytes) -> ControlledRunPlanV2:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise ContractError("ControlledRunPlanV2 must be a JSON object")
        plan = cls.model_validate(payload)
        if data != plan.canonical_bytes():
            raise ContractError("ControlledRunPlanV2 is not canonical JSON")
        return plan

    @classmethod
    def from_file(cls, path: Path) -> ControlledRunPlanV2:
        return cls.from_bytes(read_regular_file_bytes(path))

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def expand_trial_slots(self) -> tuple[ControlledTrialSlotV2, ...]:
        slots: list[ControlledTrialSlotV2] = []
        if self.companion_version == "0.3.0":
            coordinates = tuple(
                (cell, repetition)
                for cell in self.cells
                for repetition in range(1, self.repetitions + 1)
            )
        else:
            cells = {(item.case_id, item.configuration_id): item for item in self.cells}
            ordered: list[tuple[RunPlanCell, int]] = []
            for repetition in range(1, self.repetitions + 1):
                for case_index, case in enumerate(self.cases):
                    configuration_order = (
                        ("baseline", "candidate")
                        if (case_index + repetition) % 2 == 1
                        else ("candidate", "baseline")
                    )
                    ordered.extend(
                        (cells[(case.case_id, configuration_id)], repetition)
                        for configuration_id in configuration_order
                    )
            coordinates = tuple(ordered)
        for cell, repetition in coordinates:
            identity = {
                "case_id": cell.case_id,
                "configuration_id": cell.configuration_id,
                "experiment_id": cell.experiment_id,
                "repetition": repetition,
                "run_plan_id": self.run_plan_id,
            }
            slots.append(
                ControlledTrialSlotV2(
                    schema_version="cernora.reference.controlled-trial-slot/v2",
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


def materialize_controlled_run_plan(
    payload_without_id: Mapping[str, object],
) -> ControlledRunPlanV2:
    if "run_plan_id" in payload_without_id:
        raise ContractError("ControlledRunPlanV2 materialization input must omit identity")
    payload = dict(payload_without_id)
    payload["run_plan_id"] = ControlledRunPlanV2.compute_run_plan_id(payload)
    return ControlledRunPlanV2.model_validate(payload)


__all__ = [
    "ControlledRunAnalysisPolicy",
    "ControlledRunPlanV2",
    "ControlledTrialSlotV2",
    "materialize_controlled_run_plan",
]
