"""Compatibility binding from Controlled Study authority to existing Core-facing plans."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StrictStr, field_validator, model_validator

from cernora_reference_workflow.common import ContractError, canonical_content_id
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.controlled_study import StudyIntent, StudyProtocol
from cernora_reference_workflow.experiment_spec import Digest, StrictContract

Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]


def case_authority_sha256(run_plan: ControlledRunPlanV2, case_id: str) -> Digest:
    """Derive one Study Case authority from the existing Core Case projection."""

    specification = next(
        (item for item in run_plan.experiment_specs if item.task.task_id == case_id), None
    )
    if specification is None:
        raise ContractError(f"ControlledRunPlan has no Case authority: {case_id}")
    return canonical_content_id(
        specification.core_authority().case.model_dump(mode="json"),
        excluded=frozenset(),
    )


def configuration_authority_sha256(run_plan: ControlledRunPlanV2, configuration_id: str) -> Digest:
    """Derive one configuration-global Study authority from existing V2 projections."""

    specifications = tuple(
        item for item in run_plan.experiment_specs if item.configuration_id == configuration_id
    )
    if not specifications:
        raise ContractError(f"ControlledRunPlan has no Configuration authority: {configuration_id}")
    projections = {
        canonical_content_id(
            item.core_projection().model_dump(mode="json", exclude={"evaluation_authority_sha256"}),
            excluded=frozenset(),
        )
        for item in specifications
    }
    if len(projections) != 1:
        raise ContractError("ControlledRunPlan Configuration authority differs across Cases")
    projection_sha256 = next(iter(projections))
    return canonical_content_id(
        {
            "configuration_id": configuration_id,
            "core_projection_sha256": projection_sha256,
        },
        excluded=frozenset(),
    )


class StudyRunPlanBinding(StrictContract):
    """Closed proof that one Study Protocol and ControlledRunPlan are the same matrix."""

    schema_version: Literal["cernora.reference.study-run-plan-binding/v1"]
    binding_id: Digest
    intent_id: Digest
    protocol_id: Digest
    run_plan_id: Digest
    ordered_trial_slot_ids: tuple[Digest, ...] = Field(min_length=1)

    @field_validator("ordered_trial_slot_ids", mode="before")
    @classmethod
    def tuple_trial_slot_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        if len(self.ordered_trial_slot_ids) != len(set(self.ordered_trial_slot_ids)):
            raise ValueError("Study RunPlan binding Trial identities must be unique")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"binding_id"})
        )
        if self.binding_id != expected:
            raise ValueError("Study RunPlan binding identity does not match canonical content")
        return self


def bind_study_run_plan(
    intent: StudyIntent,
    protocol: StudyProtocol,
    run_plan: ControlledRunPlanV2,
) -> StudyRunPlanBinding:
    """Fail closed unless existing V2/Core authority is exactly the frozen Study Protocol."""

    if protocol.source_intent_id != intent.intent_id:
        raise ContractError("Study Protocol does not derive from the supplied Intent")
    if run_plan.companion_version not in {"0.4.0", "0.4.2"} or (
        run_plan.analysis.method_version != "m4"
    ):
        raise ContractError(
            "Controlled Study requires a frozen Companion 0.4.0/0.4.2 plan boundary"
        )
    if tuple(item.configuration_id for item in run_plan.configurations) != (
        "baseline",
        "candidate",
    ):
        raise ContractError("Controlled Study requires Baseline and Candidate Configurations")
    if (
        run_plan.repetitions != intent.repetitions
        or run_plan.planned_trial_count != protocol.planned_trial_count
        or run_plan.execution.max_attempt_count != intent.max_attempt_count
        or run_plan.execution.max_total_wall_time_seconds != intent.max_wall_seconds
    ):
        raise ContractError("Study budgets or derived Trial counts do not match the RunPlan")

    plan_case_ids = tuple(item.case_id for item in run_plan.cases)
    intent_case_ids = tuple(item.case_id for item in intent.cases)
    if plan_case_ids != intent_case_ids:
        raise ContractError("Study Cases do not equal the ControlledRunPlan Cases")
    for case in intent.cases:
        if case.authority_sha256 != case_authority_sha256(run_plan, case.case_id):
            raise ContractError(f"Case authority does not match the RunPlan: {case.case_id}")

    development = intent.candidate_development
    if development.baseline.authority_sha256 != configuration_authority_sha256(
        run_plan, "baseline"
    ) or development.candidate.authority_sha256 != configuration_authority_sha256(
        run_plan, "candidate"
    ):
        raise ContractError("Candidate Development authority does not match the RunPlan")

    protocol_coordinates = tuple(
        (block.case_id, configuration_id, block.repetition)
        for block in protocol.blocks
        for configuration_id in block.configuration_order
    )
    slots = run_plan.expand_trial_slots()
    run_plan_coordinates = tuple(
        (item.case_id, item.configuration_id, item.repetition) for item in slots
    )
    if protocol_coordinates != run_plan_coordinates:
        raise ContractError("Study schedule does not equal the ControlledRunPlan Trial order")

    payload: dict[str, object] = {
        "schema_version": "cernora.reference.study-run-plan-binding/v1",
        "intent_id": intent.intent_id,
        "protocol_id": protocol.protocol_id,
        "run_plan_id": run_plan.run_plan_id,
        "ordered_trial_slot_ids": [item.trial_slot_id for item in slots],
    }
    payload["binding_id"] = canonical_content_id(payload, excluded=frozenset())
    return StudyRunPlanBinding.model_validate(payload)


__all__ = [
    "StudyRunPlanBinding",
    "bind_study_run_plan",
    "case_authority_sha256",
    "configuration_authority_sha256",
]
