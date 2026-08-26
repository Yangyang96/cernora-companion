"""Assemble one Core controlled comparison from frozen V2 authorities."""

from __future__ import annotations

import json
from pathlib import Path

from cernora import (
    BatchInput,
    BatchSummaryPackage,
    ComparisonArm,
    ComparisonCase,
    ComparisonInput,
    ComparisonSummary,
    materialize_comparison_input,
    reload_batch_summary_package,
    summarize_comparison,
)

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    load_json_bytes,
    read_regular_file_bytes,
)
from cernora_reference_workflow.comparison_plan import ComparisonPlanV1
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2


class ComparisonConfigurationError(ContractError):
    """A complete input cannot satisfy its predeclared comparison authority."""


def _validate_receipt_bindings(batch_input: BatchInput, run_plan: ControlledRunPlanV2) -> None:
    specifications = {
        (item.task.task_id, item.configuration_id): item for item in run_plan.experiment_specs
    }
    for trial in batch_input.trials:
        evaluation = trial.attempts[-1].evaluation
        if evaluation is None:
            continue
        receipt = json.loads(evaluation.file_payloads()["evaluation-receipt.json"])
        if not isinstance(receipt, dict):
            raise ContractError("strict Evaluation receipt is not a JSON object")
        specification = specifications[(trial.case_id, trial.configuration_id)]
        expected_authority = specification.expected_evaluation_authority.model_dump(mode="json")
        if receipt.get("authority") != expected_authority:
            raise ComparisonConfigurationError(
                f"Evaluation authority does not match the controlled Experiment: {trial.case_id}"
            )
        authority = receipt["authority"]
        assert isinstance(authority, dict)
        policy_payload = {
            "schema_version": "agent.evaluator.comparison-evaluation-policy/v1",
            "profile": receipt["profile"],
            "projection": authority["projection"],
            "scorer": receipt["scorer"],
            "case_gate": receipt["case_gate"],
        }
        policy_sha256 = canonical_content_id(policy_payload, excluded=frozenset())
        if policy_sha256 != specification.expected_evaluation_policy.policy_sha256:
            raise ComparisonConfigurationError(
                f"Evaluation policy does not match the controlled Experiment: {trial.case_id}"
            )


def _validate_batch_binding(batch_input: BatchInput, run_plan: ControlledRunPlanV2) -> None:
    if batch_input.run_plan_id != run_plan.run_plan_id:
        raise ContractError("Batch Input does not bind the controlled RunPlan")
    if batch_input.companion_version != run_plan.companion_version:
        raise ContractError("Batch Input Companion version does not bind the controlled RunPlan")
    expected = tuple(
        (
            item.slot_index,
            item.trial_slot_id,
            item.case_id,
            item.configuration_id,
            item.experiment_id,
            item.repetition,
        )
        for item in run_plan.expand_trial_slots()
    )
    planned = tuple(
        (
            item.slot_index,
            item.trial_slot_id,
            item.case_id,
            item.configuration_id,
            item.experiment_id,
            item.repetition,
        )
        for item in batch_input.planned_trials
    )
    if planned != expected:
        raise ContractError("Batch Input Trial slots do not equal the controlled RunPlan")


def assemble_comparison_input(
    batch_package: BatchSummaryPackage,
    run_plan: ControlledRunPlanV2,
    comparison_plan: ComparisonPlanV1,
) -> ComparisonInput:
    """Derive every Core comparison authority from strict pre-run V2 contracts."""

    try:
        comparison_plan.validate_run_plan(run_plan)
    except ContractError as exc:
        raise ComparisonConfigurationError(str(exc)) from exc
    _validate_batch_binding(batch_package.batch_input, run_plan)
    _validate_receipt_bindings(batch_package.batch_input, run_plan)
    if batch_package.summary.run_plan_id != run_plan.run_plan_id:
        raise ContractError("Batch Summary does not bind the controlled RunPlan")

    specifications = {
        (item.task.task_id, item.configuration_id): item for item in run_plan.experiment_specs
    }
    authorities = tuple(
        specifications[(case.case_id, configuration.configuration_id)].core_authority()
        for case in run_plan.cases
        for configuration in run_plan.configurations
    )
    splits = {item.case_id: item.split_id for item in comparison_plan.case_splits}
    cases: list[ComparisonCase] = []
    for case in run_plan.cases:
        baseline = specifications[
            (case.case_id, comparison_plan.baseline_configuration_id)
        ].core_authority()
        candidate = specifications[
            (case.case_id, comparison_plan.candidate_configuration_id)
        ].core_authority()
        if baseline.case != candidate.case:
            raise ContractError("comparison arms do not bind the same Case authority")
        cases.append(
            ComparisonCase(
                case=baseline.case,
                split_id=splits[case.case_id],
                baseline_experiment_id=baseline.experiment_id,
                candidate_experiment_id=candidate.experiment_id,
            )
        )

    try:
        treatment = comparison_plan.materialize_core_treatment(run_plan)
    except ContractError as exc:
        raise ComparisonConfigurationError(str(exc)) from exc

    payload: dict[str, object] = {
        "schema_version": "agent.evaluator.comparison-input/v1",
        "batch_input": batch_package.batch_input.model_dump(mode="json"),
        "baseline": ComparisonArm(
            configuration_id=comparison_plan.baseline_configuration_id
        ).model_dump(mode="json"),
        "candidate": ComparisonArm(
            configuration_id=comparison_plan.candidate_configuration_id
        ).model_dump(mode="json"),
        "experiment_authorities": [item.model_dump(mode="json") for item in authorities],
        "cases": [item.model_dump(mode="json") for item in cases],
        "treatment": treatment.model_dump(mode="json"),
        "primary_outcome": comparison_plan.primary_outcome.model_dump(mode="json"),
        "guardrails": [item.model_dump(mode="json") for item in comparison_plan.guardrails],
        "bootstrap": comparison_plan.bootstrap.model_dump(mode="json"),
        "pass_k": (
            None
            if comparison_plan.pass_k is None
            else comparison_plan.pass_k.model_dump(mode="json")
        ),
    }
    return materialize_comparison_input(payload)


def assemble_comparison_input_from_paths(
    batch_summary_root: Path,
    controlled_run_plan_path: Path,
    comparison_plan_path: Path,
) -> ComparisonInput:
    """Strictly reload all inputs before authority assembly."""

    batch_package = reload_batch_summary_package(batch_summary_root)
    run_plan_data = read_regular_file_bytes(controlled_run_plan_path)
    run_plan_payload = load_json_bytes(run_plan_data)
    if not isinstance(run_plan_payload, dict) or run_plan_payload.get("schema_version") != (
        "cernora.reference.controlled-run-plan/v2"
    ):
        raise ComparisonConfigurationError("comparison requires a controlled RunPlan v2")
    run_plan = ControlledRunPlanV2.from_bytes(run_plan_data)
    plan_data = read_regular_file_bytes(comparison_plan_path)
    plan_payload = load_json_bytes(plan_data)
    if not isinstance(plan_payload, dict) or plan_payload.get("schema_version") != (
        "cernora.reference.comparison-plan/v1"
    ):
        raise ComparisonConfigurationError("comparison requires ComparisonPlan v1")
    comparison_plan = ComparisonPlanV1.from_bytes(plan_data)
    return assemble_comparison_input(batch_package, run_plan, comparison_plan)


def compare_batch_summary(
    batch_summary_root: Path,
    controlled_run_plan_path: Path,
    comparison_plan_path: Path,
    output: Path,
) -> ComparisonSummary:
    """Publish and strict-reload one deterministic Core Comparison package."""

    comparison_input = assemble_comparison_input_from_paths(
        batch_summary_root,
        controlled_run_plan_path,
        comparison_plan_path,
    )
    return summarize_comparison(comparison_input, output)


__all__ = [
    "ComparisonConfigurationError",
    "assemble_comparison_input",
    "assemble_comparison_input_from_paths",
    "compare_batch_summary",
]
