"""Strict predeclared ComparisonPlan bound to one controlled RunPlan."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from cernora import (
    BootstrapPlan,
    ComparisonGuardrail,
    PassKPlan,
    PrimaryOutcome,
    Treatment,
    TreatmentChange,
    materialize_treatment,
)
from pydantic import Field, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    load_json_bytes,
    read_regular_file_bytes,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    Digest,
    Identifier,
    StatisticalPolicy,
    StrictV2Contract,
)
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2

TreatmentKind = Literal[
    "prompt_instruction",
    "model",
    "tool_schema",
    "generation_configuration",
    "runtime_version",
]

_PROJECTION_FIELD: dict[TreatmentKind, str] = {
    "prompt_instruction": "prompt_instruction_sha256",
    "model": "model_sha256",
    "tool_schema": "tool_schema_sha256",
    "generation_configuration": "generation_configuration_sha256",
    "runtime_version": "runtime_version_sha256",
}
_FIELD_TREATMENT = {field: kind for kind, field in _PROJECTION_FIELD.items()}


class ComparisonPlanCaseSplit(StrictV2Contract):
    case_id: Identifier
    split_id: Identifier


class TreatmentDeclaration(StrictV2Contract):
    """Kinds are frozen before endpoint digests are derived from V2 authorities."""

    schema_version: Literal["cernora.reference.treatment-declaration/v1"]
    declaration_id: str = Field(min_length=1)
    declaration_sha256: Digest
    kinds: Annotated[tuple[TreatmentKind, ...], Field(min_length=1)]

    @field_validator("kinds", mode="before")
    @classmethod
    def tuple_kinds(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def identity_and_order(self) -> Self:
        if self.kinds != tuple(sorted(self.kinds)) or len(self.kinds) != len(set(self.kinds)):
            raise ValueError("Treatment kinds must be sorted, unique, and non-empty")
        payload = self.model_dump(mode="json", exclude={"declaration_id", "declaration_sha256"})
        digest = canonical_content_id(payload, excluded=frozenset())
        if self.declaration_sha256 != digest or (
            self.declaration_id != f"treatment-declaration-{digest}"
        ):
            raise ValueError("Treatment declaration identity is not canonical")
        return self


def materialize_treatment_declaration(kinds: tuple[TreatmentKind, ...]) -> TreatmentDeclaration:
    ordered = tuple(sorted(kinds))
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.treatment-declaration/v1",
        "kinds": list(ordered),
    }
    digest = canonical_content_id(payload, excluded=frozenset())
    payload["declaration_id"] = f"treatment-declaration-{digest}"
    payload["declaration_sha256"] = digest
    return TreatmentDeclaration.model_validate(payload)


class ComparisonPlanV1(StrictV2Contract):
    schema_version: Literal["cernora.reference.comparison-plan/v1"]
    comparison_plan_id: str = Field(min_length=1)
    comparison_plan_sha256: Digest
    source_run_plan_id: Digest
    baseline_configuration_id: Identifier
    candidate_configuration_id: Identifier
    case_splits: Annotated[tuple[ComparisonPlanCaseSplit, ...], Field(min_length=1)]
    treatment: TreatmentDeclaration
    primary_outcome: PrimaryOutcome
    guardrails: Annotated[tuple[ComparisonGuardrail, ...], Field(min_length=1)]
    bootstrap: BootstrapPlan
    pass_k: PassKPlan | None
    statistical_policy: StatisticalPolicy

    @field_validator("case_splits", "guardrails", mode="before")
    @classmethod
    def tuple_collections(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_declaration(self) -> Self:
        if self.baseline_configuration_id == self.candidate_configuration_id:
            raise ValueError("ComparisonPlan requires two distinct Configurations")
        case_ids = tuple(item.case_id for item in self.case_splits)
        if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("ComparisonPlan Case splits must be sorted and exhaustive by Case")
        guardrail_ids = tuple(item.guardrail_id for item in self.guardrails)
        if guardrail_ids != tuple(sorted(guardrail_ids)) or len(guardrail_ids) != len(
            set(guardrail_ids)
        ):
            raise ValueError("ComparisonPlan Guardrails must be sorted and unique")
        split_ids = {item.split_id for item in self.case_splits}
        if self.primary_outcome.scope == "split" and self.primary_outcome.split_id not in split_ids:
            raise ValueError("ComparisonPlan Primary Outcome references an unknown split")
        if any(
            item.scope == "split" and item.split_id not in split_ids for item in self.guardrails
        ):
            raise ValueError("ComparisonPlan Guardrail references an unknown split")
        if self.primary_outcome.metric != "reliable_success_rate":
            raise ValueError("ComparisonPlan Primary Outcome must be Reliable Success Rate")
        if self.bootstrap != self.statistical_policy.bootstrap or (
            self.pass_k != self.statistical_policy.pass_k
        ):
            raise ValueError("ComparisonPlan statistics do not match the independent policy")
        payload = self.model_dump(
            mode="json", exclude={"comparison_plan_id", "comparison_plan_sha256"}
        )
        digest = canonical_content_id(payload, excluded=frozenset())
        if self.comparison_plan_sha256 != digest or (
            self.comparison_plan_id != f"comparison-plan-{digest}"
        ):
            raise ValueError("ComparisonPlan identity is not canonical")
        return self

    @classmethod
    def from_bytes(cls, data: bytes) -> ComparisonPlanV1:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise ContractError("ComparisonPlanV1 must be a JSON object")
        plan = cls.model_validate(payload)
        if data != plan.canonical_bytes():
            raise ContractError("ComparisonPlanV1 is not canonical JSON")
        return plan

    @classmethod
    def from_file(cls, path: Path) -> ComparisonPlanV1:
        return cls.from_bytes(read_regular_file_bytes(path))

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def validate_run_plan(self, run_plan: ControlledRunPlanV2) -> None:
        if not isinstance(run_plan, ControlledRunPlanV2):
            raise ContractError("ComparisonPlan requires a controlled RunPlan v2")
        if run_plan.run_plan_id != self.source_run_plan_id:
            raise ContractError("ComparisonPlan does not bind the supplied controlled RunPlan")
        selected = (
            self.baseline_configuration_id,
            self.candidate_configuration_id,
        )
        if set(selected) != {item.configuration_id for item in run_plan.configurations}:
            raise ContractError("ComparisonPlan Configurations do not exhaust the RunPlan")
        if tuple(item.case_id for item in self.case_splits) != tuple(
            item.case_id for item in run_plan.cases
        ):
            raise ContractError("ComparisonPlan Case splits do not exhaust the RunPlan")
        if any(
            spec.statistical_policy != self.statistical_policy for spec in run_plan.experiment_specs
        ):
            raise ContractError("RunPlan Experiment statistics do not match ComparisonPlan")
        projections: dict[str, dict[str, str]] = {}
        for configuration_id in selected:
            candidates = [
                spec.core_projection().model_dump(
                    mode="json", exclude={"evaluation_authority_sha256"}
                )
                for spec in run_plan.experiment_specs
                if spec.configuration_id == configuration_id
            ]
            if not candidates or any(item != candidates[0] for item in candidates[1:]):
                raise ContractError(
                    "configuration-global Experiment projection differs across Cases"
                )
            projections[configuration_id] = candidates[0]
        baseline = projections[self.baseline_configuration_id]
        candidate = projections[self.candidate_configuration_id]
        differing_fields = {field for field in baseline if baseline[field] != candidate[field]}
        invariant_differences = differing_fields - set(_FIELD_TREATMENT)
        if invariant_differences:
            raise ContractError(
                "controlled arms differ outside Treatment: "
                + ", ".join(sorted(invariant_differences))
            )
        actual_kinds = {_FIELD_TREATMENT[field] for field in differing_fields}
        if actual_kinds != set(self.treatment.kinds):
            raise ContractError("declared Treatment does not equal authority-derived differences")

    def materialize_core_treatment(self, run_plan: ControlledRunPlanV2) -> Treatment:
        self.validate_run_plan(run_plan)
        specs = {
            (item.task.task_id, item.configuration_id): item for item in run_plan.experiment_specs
        }
        changes: list[TreatmentChange] = []
        for kind in self.treatment.kinds:
            field = _PROJECTION_FIELD[kind]
            endpoints = {
                (
                    getattr(
                        specs[(case.case_id, self.baseline_configuration_id)].core_projection(),
                        field,
                    ),
                    getattr(
                        specs[(case.case_id, self.candidate_configuration_id)].core_projection(),
                        field,
                    ),
                )
                for case in self.case_splits
            }
            if len(endpoints) != 1:
                raise ContractError("Treatment endpoints are not coherent across Cases")
            baseline_sha256, candidate_sha256 = endpoints.pop()
            if baseline_sha256 == candidate_sha256:
                raise ContractError("predeclared Treatment kind has no authority-derived change")
            changes.append(
                TreatmentChange(
                    kind=kind,
                    baseline_sha256=baseline_sha256,
                    candidate_sha256=candidate_sha256,
                )
            )
        return materialize_treatment(changes)


def materialize_comparison_plan(payload_without_identity: Mapping[str, object]) -> ComparisonPlanV1:
    if {"comparison_plan_id", "comparison_plan_sha256"}.intersection(payload_without_identity):
        raise ContractError("ComparisonPlanV1 materialization input must omit identity")
    payload = dict(payload_without_identity)
    digest = canonical_content_id(payload, excluded=frozenset())
    payload["comparison_plan_id"] = f"comparison-plan-{digest}"
    payload["comparison_plan_sha256"] = digest
    return ComparisonPlanV1.model_validate(payload)


__all__ = [
    "ComparisonPlanCaseSplit",
    "ComparisonPlanV1",
    "TreatmentDeclaration",
    "TreatmentKind",
    "materialize_comparison_plan",
    "materialize_treatment_declaration",
]
