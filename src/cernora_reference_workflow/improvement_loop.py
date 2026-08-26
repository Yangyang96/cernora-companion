"""Fail-closed development selection and immutable Candidate freezing."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from cernora import BatchInput, BatchSummary, BatchSummaryPackage, ComparisonInput
from pydantic import Field, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    closed_regular_tree,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.comparison_input import assemble_comparison_input
from cernora_reference_workflow.comparison_plan import ComparisonPlanV1
from cernora_reference_workflow.controlled_experiment_spec import (
    CanonicalAuthoritySource,
    Digest,
    Identifier,
    StrictV2Contract,
)
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2


class LeadingFailure(StrictV2Contract):
    profile_id: Identifier
    profile_version: str = Field(min_length=1)
    code: Identifier
    count: Annotated[int, Field(gt=0)]


class DevelopmentPilotBinding(StrictV2Contract):
    """Separate offline lineage; never a Trial source for the final live Batch."""

    pilot_kind: Literal["deterministic_offline_seed_receipts"]
    pilot_id: Digest
    run_plan_id: Digest
    batch_input_id: str = Field(min_length=1)
    batch_input_sha256: Digest
    summary_id: str = Field(min_length=1)
    summary_sha256: Digest
    development_case_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    prohibited_from_final_live_batch: Literal[True]

    @field_validator("development_case_ids", mode="before")
    @classmethod
    def tuple_cases(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def canonical_pilot(self) -> Self:
        if self.development_case_ids != tuple(sorted(self.development_case_ids)) or len(
            self.development_case_ids
        ) != len(set(self.development_case_ids)):
            raise ValueError("development pilot Cases must be sorted and unique")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"pilot_id"})
        )
        if self.pilot_id != expected:
            raise ValueError("development pilot identity mismatch")
        return self


class FrozenM4Policy(StrictV2Contract):
    schema_version: Literal["cernora.reference.m4-policy/v1"]
    primary_metric: Literal["reliable_success_rate"]
    practical_threshold_basis_points: Literal[1000]
    evaluation_validity_max_adverse_basis_points: Literal[0]
    protected_path_failure_max_adverse_basis_points: Literal[0]
    regression_reliable_success_max_adverse_basis_points: Literal[1000]
    improved_requires_interval_above_zero: Literal[True]
    improved_requires_all_hard_guardrails: Literal[True]


class CandidateFreeze(StrictV2Contract):
    """One content-identified Prompt/Instruction change selected without held-out access."""

    schema_version: Literal["cernora.reference.candidate-freeze/v1"]
    candidate_freeze_id: str = Field(min_length=1)
    candidate_freeze_sha256: Digest
    pilot: DevelopmentPilotBinding
    leading_failure: LeadingFailure
    treatment_kind: Literal["prompt_instruction"]
    baseline_prompt_authority: CanonicalAuthoritySource
    candidate_prompt_authority: CanonicalAuthoritySource
    policy: FrozenM4Policy
    visible_corpus_sha256: Digest
    heldout_seal_manifest_id: str = Field(min_length=1)
    heldout_seal_manifest_sha256: Digest

    @model_validator(mode="after")
    def canonical_single_change(self) -> Self:
        if self.baseline_prompt_authority.source_sha256 == (
            self.candidate_prompt_authority.source_sha256
        ):
            raise ValueError("CandidateFreeze requires one substantive Prompt/Instruction change")
        payload = self.model_dump(
            mode="json", exclude={"candidate_freeze_id", "candidate_freeze_sha256"}
        )
        digest = canonical_content_id(payload, excluded=frozenset())
        if self.candidate_freeze_sha256 != digest or self.candidate_freeze_id != (
            f"candidate-freeze-{digest}"
        ):
            raise ValueError("CandidateFreeze identity is not canonical")
        return self


def select_leading_failure(
    pilot_batch: BatchInput,
    pilot_summary: BatchSummary,
    *,
    development_case_ids: tuple[str, ...],
) -> LeadingFailure:
    """Select only from one separately identified development-baseline pilot."""

    expected_cases = tuple(sorted(development_case_ids))
    actual_cases = tuple(item.case_id for item in pilot_summary.by_case if item.case_id is not None)
    if not expected_cases or actual_cases != expected_cases:
        raise ContractError("development pilot must contain exactly the declared development Cases")
    if {item.case_id for item in pilot_batch.planned_trials} != set(expected_cases):
        raise ContractError("development pilot Batch contains non-development Trial slots")
    configurations = {item.configuration_id for item in pilot_batch.planned_trials}
    if len(configurations) != 1:
        raise ContractError("development pilot must contain baseline Configuration only")
    if (
        pilot_summary.batch_input_id != pilot_batch.batch_input_id
        or pilot_summary.batch_input_sha256 != pilot_batch.batch_input_sha256
    ):
        raise ContractError("development pilot Summary does not bind its Batch Input")
    ordered = sorted(
        pilot_summary.profile_failure_codes,
        key=lambda item: (-item.count, item.profile_id, item.profile_version, item.code),
    )
    if not ordered:
        raise ContractError("development baseline has no usable versioned failure")
    selected = ordered[0]
    return LeadingFailure(
        profile_id=selected.profile_id,
        profile_version=selected.profile_version,
        code=selected.code,
        count=selected.count,
    )


def materialize_development_pilot(
    *,
    run_plan_id: str,
    batch_input: BatchInput,
    summary: BatchSummary,
    development_case_ids: tuple[str, ...],
) -> DevelopmentPilotBinding:
    payload: dict[str, object] = {
        "pilot_kind": "deterministic_offline_seed_receipts",
        "run_plan_id": run_plan_id,
        "batch_input_id": batch_input.batch_input_id,
        "batch_input_sha256": batch_input.batch_input_sha256,
        "summary_id": summary.summary_id,
        "summary_sha256": summary.summary_sha256,
        "development_case_ids": list(sorted(development_case_ids)),
        "prohibited_from_final_live_batch": True,
    }
    payload["pilot_id"] = canonical_content_id(payload, excluded=frozenset())
    return DevelopmentPilotBinding.model_validate(payload)


def materialize_candidate_freeze(
    payload_without_identity: Mapping[str, object],
) -> CandidateFreeze:
    if {"candidate_freeze_id", "candidate_freeze_sha256"}.intersection(payload_without_identity):
        raise ContractError("CandidateFreeze materialization input must omit identity")
    payload = dict(payload_without_identity)
    digest = canonical_content_id(payload, excluded=frozenset())
    payload["candidate_freeze_id"] = f"candidate-freeze-{digest}"
    payload["candidate_freeze_sha256"] = digest
    return CandidateFreeze.model_validate(payload)


def visible_corpus_digest(root: Path) -> str:
    """Digest the closed visible corpus by canonical path, size, and stable bytes."""

    files = closed_regular_tree(root)
    entries = []
    for relative, path in files.items():
        payload = read_regular_file_bytes(path)
        entries.append(
            {"path": relative, "sha256": sha256_bytes(payload), "size_bytes": len(payload)}
        )
    if not entries:
        raise ContractError("visible corpus must contain at least one ordinary file")
    return canonical_content_id({"files": entries}, excluded=frozenset())


def assemble_final_comparison_input(
    batch_package: BatchSummaryPackage,
    run_plan: ControlledRunPlanV2,
    comparison_plan: ComparisonPlanV1,
) -> ComparisonInput:
    """Delegate final statistics and conclusion to the accepted M3/Core seam."""

    return assemble_comparison_input(batch_package, run_plan, comparison_plan)


__all__ = [
    "CandidateFreeze",
    "DevelopmentPilotBinding",
    "FrozenM4Policy",
    "LeadingFailure",
    "assemble_final_comparison_input",
    "materialize_candidate_freeze",
    "materialize_development_pilot",
    "select_leading_failure",
    "visible_corpus_digest",
]
