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
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.comparison_input import assemble_comparison_input
from cernora_reference_workflow.comparison_plan import ComparisonPlanV1
from cernora_reference_workflow.controlled_evaluation import (
    RepairResultRecord,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    CanonicalAuthoritySource,
    Digest,
    Identifier,
    StrictV2Contract,
)
from cernora_reference_workflow.controlled_profile import PROFILE_ID, PROFILE_VERSION
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.heldout_seal import (
    HeldoutManifest,
    HeldoutRevealReceipt,
)


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
        baseline_payload = self.baseline_prompt_authority.payload
        candidate_payload = self.candidate_prompt_authority.payload
        if not isinstance(baseline_payload, dict) or set(baseline_payload) != {"text"}:
            raise ValueError("baseline Prompt authority must contain exactly one text field")
        if not isinstance(candidate_payload, dict) or set(candidate_payload) != {
            "selected_failure",
            "text",
        }:
            raise ValueError("Candidate Prompt authority must explicitly bind selected_failure")
        selected = candidate_payload["selected_failure"]
        expected_selected = {
            "code": self.leading_failure.code,
            "profile_id": self.leading_failure.profile_id,
            "profile_version": self.leading_failure.profile_version,
        }
        if selected != expected_selected:
            raise ValueError("Candidate Prompt selected_failure does not equal LeadingFailure")
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


def _materialize_candidate_freeze(
    payload_without_identity: Mapping[str, object],
) -> CandidateFreeze:
    if {"candidate_freeze_id", "candidate_freeze_sha256"}.intersection(payload_without_identity):
        raise ContractError("CandidateFreeze materialization input must omit identity")
    payload = dict(payload_without_identity)
    digest = canonical_content_id(payload, excluded=frozenset())
    payload["candidate_freeze_id"] = f"candidate-freeze-{digest}"
    payload["candidate_freeze_sha256"] = digest
    return CandidateFreeze.model_validate(payload)


def _pilot_repair_result(trial: object) -> RepairResultRecord:
    attempts = getattr(trial, "attempts", None)
    if not isinstance(attempts, tuple) or not attempts:
        raise ContractError("development pilot Trial has no strict Attempt chain")
    package = attempts[-1].evaluation
    if package is None:
        raise ContractError("development pilot requires evaluated seed receipts")
    files = package.file_payloads()
    receipt_raw = files.get("evaluation-receipt.json")
    report_raw = files.get("evaluation-report.json")
    if receipt_raw is None or report_raw is None:
        raise ContractError("development pilot omits authoritative evaluation receipts")
    receipt = load_json_bytes(receipt_raw)
    report = load_json_bytes(report_raw)
    if not isinstance(receipt, dict) or not isinstance(report, dict):
        raise ContractError("development pilot evaluation receipts are not JSON objects")
    profile = receipt.get("profile")
    case = receipt.get("case")
    if not isinstance(profile, dict) or (
        profile.get("profile_id") != PROFILE_ID or profile.get("profile_version") != PROFILE_VERSION
    ):
        raise ContractError("development pilot does not use the controlled repair Profile")
    if not isinstance(case, dict) or case.get("case_id") != getattr(trial, "case_id", None):
        raise ContractError("development pilot evaluation receipt binds another Case")
    if report.get("evaluation_validity") != "valid":
        raise ContractError("development pilot evaluation is not valid")
    path = "source-import/artifacts/evidence/repair-result.json"
    raw = files.get(path)
    if raw is None:
        raise ContractError("development pilot Evaluation Package omits repair receipt")
    payload = load_json_bytes(raw)
    if not isinstance(payload, dict):
        raise ContractError("development pilot repair receipt is not one JSON object")
    result = RepairResultRecord.model_validate(payload)
    if raw != canonical_json_bytes(result.model_dump(mode="json")):
        raise ContractError("development pilot repair receipt is not canonical JSON")
    return result


def derive_candidate_freeze(
    *,
    pilot_package: BatchSummaryPackage,
    baseline_prompt_authority: CanonicalAuthoritySource,
    candidate_prompt_authority: CanonicalAuthoritySource,
    visible_corpus_root: Path,
    heldout_manifest: HeldoutManifest,
) -> CandidateFreeze:
    """Derive the accepted Freeze from three real development seed receipts."""

    batch = pilot_package.batch_input
    summary = pilot_package.summary
    if len(batch.trials) != 3 or len(batch.planned_trials) != 3:
        raise ContractError("development pilot requires exactly three seed Trials")
    if len({item.configuration_id for item in batch.trials}) != 1 or {
        item.configuration_id for item in batch.trials
    } != {"baseline"}:
        raise ContractError("development pilot must contain baseline Configuration only")
    if len({item.case_id for item in batch.trials}) != 3:
        raise ContractError("development pilot requires three distinct development Cases")
    results = tuple(_pilot_repair_result(item) for item in batch.trials)
    if any(
        result.case_id != trial.case_id for result, trial in zip(results, batch.trials, strict=True)
    ):
        raise ContractError("development pilot repair receipt binds another Case")
    counts: dict[str, int] = {}
    for result in results:
        for code in result.failure_codes:
            counts[code] = counts.get(code, 0) + 1
    if not counts:
        raise ContractError("development baseline has no usable versioned failure")
    code, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    leading = LeadingFailure(
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
        code=code,
        count=count,
    )
    case_ids = tuple(sorted(item.case_id for item in batch.trials))
    pilot = materialize_development_pilot(
        run_plan_id=batch.run_plan_id,
        batch_input=batch,
        summary=summary,
        development_case_ids=case_ids,
    )
    policy = FrozenM4Policy(
        schema_version="cernora.reference.m4-policy/v1",
        primary_metric="reliable_success_rate",
        practical_threshold_basis_points=1000,
        evaluation_validity_max_adverse_basis_points=0,
        protected_path_failure_max_adverse_basis_points=0,
        regression_reliable_success_max_adverse_basis_points=1000,
        improved_requires_interval_above_zero=True,
        improved_requires_all_hard_guardrails=True,
    )
    return _materialize_candidate_freeze(
        {
            "schema_version": "cernora.reference.candidate-freeze/v1",
            "pilot": pilot.model_dump(mode="json"),
            "leading_failure": leading.model_dump(mode="json"),
            "treatment_kind": "prompt_instruction",
            "baseline_prompt_authority": baseline_prompt_authority.model_dump(mode="json"),
            "candidate_prompt_authority": candidate_prompt_authority.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "visible_corpus_sha256": visible_corpus_digest(visible_corpus_root),
            "heldout_seal_manifest_id": heldout_manifest.manifest_id,
            "heldout_seal_manifest_sha256": sha256_bytes(heldout_manifest.canonical_bytes()),
        }
    )


def verify_candidate_freeze(
    freeze: CandidateFreeze,
    *,
    pilot_package: BatchSummaryPackage,
    run_plan: ControlledRunPlanV2,
    comparison_plan: ComparisonPlanV1,
    visible_corpus_root: Path,
    heldout_manifest: HeldoutManifest,
    reveal_receipt: HeldoutRevealReceipt,
    task_authorities: tuple[ControlledTaskAuthority, ...],
) -> None:
    """Bind one Freeze to the exact final 54-Trial declaration and reveal."""

    if run_plan.companion_version != "0.4.0":
        raise ContractError("CandidateFreeze final RunPlan must use Companion 0.4.0")
    try:
        reconstructed = derive_candidate_freeze(
            pilot_package=pilot_package,
            baseline_prompt_authority=freeze.baseline_prompt_authority,
            candidate_prompt_authority=freeze.candidate_prompt_authority,
            visible_corpus_root=visible_corpus_root,
            heldout_manifest=heldout_manifest,
        )
    except ValueError as exc:
        raise ContractError(
            "CandidateFreeze is not the exact derivation of the strict pilot"
        ) from exc
    if reconstructed != freeze or canonical_json_bytes(
        reconstructed.model_dump(mode="json")
    ) != canonical_json_bytes(freeze.model_dump(mode="json")):
        raise ContractError("CandidateFreeze is not the exact derivation of the strict pilot")
    if freeze.visible_corpus_sha256 != visible_corpus_digest(visible_corpus_root):
        raise ContractError("CandidateFreeze does not bind the stable visible corpus")
    if (
        freeze.heldout_seal_manifest_id != heldout_manifest.manifest_id
        or freeze.heldout_seal_manifest_sha256 != sha256_bytes(heldout_manifest.canonical_bytes())
    ):
        raise ContractError("CandidateFreeze does not bind the checked-in held-out Manifest")
    if (
        reveal_receipt.manifest_id != heldout_manifest.manifest_id
        or reveal_receipt.manifest_sha256 != freeze.heldout_seal_manifest_sha256
        or reveal_receipt.candidate_freeze_id != freeze.candidate_freeze_id
        or reveal_receipt.candidate_freeze_sha256 != freeze.candidate_freeze_sha256
    ):
        raise ContractError("held-out RevealReceipt does not bind CandidateFreeze and Manifest")
    splits: dict[str, list[str]] = {}
    for item in comparison_plan.case_splits:
        splits.setdefault(item.split_id, []).append(item.case_id)
    if set(splits) != {"development", "regression", "held-out"} or any(
        len(items) != 3 for items in splits.values()
    ):
        raise ContractError("final comparison requires exact 3/3/3 Case splits")
    task_splits: dict[str, list[str]] = {}
    task_ids: set[str] = set()
    for task in task_authorities:
        if task.case.case_id in task_ids:
            raise ContractError("final task authorities contain duplicate Cases")
        task_ids.add(task.case.case_id)
        task_splits.setdefault(task.split_id, []).append(task.case.case_id)
    if task_ids != {item.case_id for item in run_plan.cases} or {
        key: tuple(sorted(value)) for key, value in task_splits.items()
    } != {key: tuple(sorted(value)) for key, value in splits.items()}:
        raise ContractError("Comparison splits do not equal strict task split authorities")
    if tuple(sorted(splits["development"])) != freeze.pilot.development_case_ids:
        raise ContractError("CandidateFreeze pilot Cases do not equal the development split")
    heldout_ids = tuple(sorted(splits["held-out"]))
    if heldout_ids != tuple(item.case_id for item in heldout_manifest.case_commitments) or (
        heldout_ids != tuple(item.case_id for item in reveal_receipt.case_records)
    ):
        raise ContractError("final held-out split does not equal Manifest and RevealReceipt")
    comparison_plan.validate_run_plan(run_plan)
    baseline_specs = tuple(
        item
        for item in run_plan.experiment_specs
        if item.configuration_id == comparison_plan.baseline_configuration_id
    )
    candidate_specs = tuple(
        item
        for item in run_plan.experiment_specs
        if item.configuration_id == comparison_plan.candidate_configuration_id
    )
    if any(
        item.prompt_source != freeze.baseline_prompt_authority for item in baseline_specs
    ) or any(item.prompt_source != freeze.candidate_prompt_authority for item in candidate_specs):
        raise ContractError("final RunPlan Prompt authorities do not equal CandidateFreeze")
    by_case = {
        (item.task.task_id, item.configuration_id): item for item in run_plan.experiment_specs
    }
    for case in run_plan.cases:
        baseline = by_case[(case.case_id, comparison_plan.baseline_configuration_id)]
        candidate = by_case[(case.case_id, comparison_plan.candidate_configuration_id)]
        if baseline.instruction_source != candidate.instruction_source:
            raise ContractError("final Treatment changes instruction outside frozen Prompt source")
        left = baseline.core_projection().model_dump(mode="json")
        right = candidate.core_projection().model_dump(mode="json")
        if {field for field in left if left[field] != right[field]} != {
            "prompt_instruction_sha256"
        }:
            raise ContractError("final arms differ outside the exact Prompt/Instruction Treatment")
    primary = comparison_plan.primary_outcome
    if primary.practical_threshold_basis_points != 1000:
        raise ContractError("final Primary Outcome threshold must be absolute +10pp")
    actual_guardrails = {
        (
            item.metric,
            item.scope,
            item.split_id,
            item.direction,
            item.max_adverse_basis_points,
            item.profile_id,
            item.profile_version,
            item.failure_code,
        )
        for item in comparison_plan.guardrails
    }
    expected_guardrails = {
        ("evaluation_validity_rate", "all", None, "higher_is_better", 0, None, None, None),
        (
            "profile_failure_code_rate",
            "all",
            None,
            "lower_is_better",
            0,
            PROFILE_ID,
            PROFILE_VERSION,
            "protected_paths_unchanged_v1",
        ),
        (
            "reliable_success_rate",
            "split",
            "regression",
            "higher_is_better",
            1000,
            None,
            None,
            None,
        ),
    }
    if actual_guardrails != expected_guardrails:
        raise ContractError("final hard Guardrails do not equal the frozen M4 policy")


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
    "derive_candidate_freeze",
    "materialize_development_pilot",
    "select_leading_failure",
    "verify_candidate_freeze",
    "visible_corpus_digest",
]
