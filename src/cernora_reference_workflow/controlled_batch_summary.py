"""Normalize a complete controlled execution into strict Core Batch contracts."""

from __future__ import annotations

from pathlib import Path

from cernora import (
    BatchAttempt,
    BatchInput,
    BatchPlannedTrial,
    BatchSummary,
    BatchTrial,
    build_batch_summary,
    materialize_batch_input,
    summarize_batch,
)

from cernora_reference_workflow.controlled_execution import ControlledExecutionResult
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2


def normalize_controlled_execution(
    execution: ControlledExecutionResult,
    plan: ControlledRunPlanV2,
) -> BatchInput:
    """Derive a path-free Core BatchInput without collapsing Attempt chains."""

    execution.verify_plan(plan)
    planned = tuple(
        BatchPlannedTrial(
            slot_index=item.slot_index,
            trial_slot_id=item.trial_slot_id,
            case_id=item.case_id,
            configuration_id=item.configuration_id,
            experiment_id=item.experiment_id,
            repetition=item.repetition,
        )
        for item in plan.expand_trial_slots()
    )
    trials: list[BatchTrial] = []
    for trial in execution.trials:
        attempts: list[BatchAttempt] = []
        for item in trial.attempts:
            attempts.append(
                BatchAttempt(
                    schema_version="agent.evaluator.batch-attempt/v1",
                    attempt_id=item.attempt_id,
                    source_attempt_id=item.source_attempt_id,
                    trial_id=trial.trial_id,
                    ordinal=item.ordinal,
                    predecessor_attempt_id=item.predecessor_attempt_id,
                    source_manifest_sha256=item.source_manifest_sha256,
                    retry_eligible=item.retry_eligible,
                    resources=item.resources,
                    evaluation=item.evaluation,
                    lifecycle=item.lifecycle,
                )
            )
        slot = trial.slot
        trials.append(
            BatchTrial(
                schema_version="agent.evaluator.batch-trial/v1",
                run_plan_id=plan.run_plan_id,
                execution_id=execution.execution_id,
                trial_id=trial.trial_id,
                slot_index=slot.slot_index,
                trial_slot_id=slot.trial_slot_id,
                case_id=slot.case_id,
                configuration_id=slot.configuration_id,
                experiment_id=slot.experiment_id,
                repetition=slot.repetition,
                selected_attempt_id=trial.selected_attempt_id,
                attempts=tuple(attempts),
            )
        )
    return materialize_batch_input(
        {
            "schema_version": "agent.evaluator.batch-input/v1",
            "run_plan_id": plan.run_plan_id,
            "execution_id": execution.execution_id,
            "execution_status": execution.status,
            "budget_status": execution.budget_status,
            "companion_version": plan.companion_version,
            "planned_trial_count": plan.planned_trial_count,
            "attempt_count": execution.attempt_count,
            "planned_trials": [item.model_dump(mode="json") for item in planned],
            "trials": [item.model_dump(mode="json") for item in trials],
        }
    )


def build_controlled_batch_summary(
    execution: ControlledExecutionResult,
    plan: ControlledRunPlanV2,
) -> BatchSummary:
    return build_batch_summary(normalize_controlled_execution(execution, plan))


def publish_controlled_batch_summary(
    execution: ControlledExecutionResult,
    plan: ControlledRunPlanV2,
    output: Path,
) -> BatchSummary:
    return summarize_batch(normalize_controlled_execution(execution, plan), output)


__all__ = [
    "build_controlled_batch_summary",
    "normalize_controlled_execution",
    "publish_controlled_batch_summary",
]
