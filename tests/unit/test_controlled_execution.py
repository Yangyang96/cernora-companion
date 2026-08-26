from __future__ import annotations

import hashlib
from typing import Literal

import pytest
from cernora import BatchAttemptResources, BatchLifecycleRecord
from pydantic import ValidationError

from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptRequest,
    ControlledTrialExecution,
    materialize_controlled_attempt,
)
from cernora_reference_workflow.controlled_run_plan import materialize_controlled_run_plan
from cernora_reference_workflow.controlled_runtime import observe_runtime_authority
from tests.unit.test_controlled_run_plan import valid_payload


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def lifecycle_attempt(
    request: ControlledAttemptRequest, *, retry_eligible: bool = False
) -> ControlledAttempt:
    category: Literal["infrastructure_start_failure", "other_verified_infrastructure_failure"] = (
        "infrastructure_start_failure"
        if retry_eligible
        else "other_verified_infrastructure_failure"
    )
    lifecycle = BatchLifecycleRecord(
        schema_version="agent.evaluator.batch-lifecycle/v1",
        category=category,
        retry_eligible=retry_eligible,
        source_state=category.replace("_", "-"),
        receipt_sha256=digest(f"receipt:{request.trial_id}:{request.ordinal}:{retry_eligible}"),
    )
    return materialize_controlled_attempt(
        {
            "schema_version": "cernora.reference.controlled-attempt/v1",
            "trial_id": request.trial_id,
            "ordinal": request.ordinal,
            "predecessor_attempt_id": request.predecessor_attempt_id,
            "source_attempt_id": digest(
                f"source:{request.trial_id}:{request.ordinal}:{retry_eligible}"
            ),
            "source_manifest_sha256": digest(
                f"manifest:{request.trial_id}:{request.ordinal}:{retry_eligible}"
            ),
            "retry_eligible": retry_eligible,
            "resources": BatchAttemptResources().model_dump(mode="json"),
            "runtime_observation": None,
            "repair_result": None,
            "evaluation": None,
            "lifecycle": lifecycle.model_dump(mode="json"),
        }
    )


def test_attempt_identity_and_runtime_authority_fail_closed() -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    slot = plan.expand_trial_slots()[0]
    spec = plan.experiment_specs[0]
    request = ControlledAttemptRequest(
        trial_id=digest("trial"),
        slot=slot,
        specification=spec,
        ordinal=1,
        predecessor_attempt_id=None,
        global_deadline_monotonic=100.0,
    )
    attempt = lifecycle_attempt(request)
    attempt.verify_authority(spec)
    with pytest.raises(ValidationError, match="identity"):
        ControlledAttempt.model_validate(
            attempt.model_copy(update={"attempt_id": "0" * 64}).model_dump(mode="json")
        )


def test_trial_rejects_unconsumed_retry_eligible_attempt() -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    slot = plan.expand_trial_slots()[0]
    request = ControlledAttemptRequest(
        trial_id=digest("trial"),
        slot=slot,
        specification=plan.experiment_specs[0],
        ordinal=1,
        predecessor_attempt_id=None,
        global_deadline_monotonic=100.0,
    )
    attempt = lifecycle_attempt(request, retry_eligible=True)
    with pytest.raises(ValidationError, match="not complete"):
        ControlledTrialExecution(
            schema_version="cernora.reference.controlled-trial-execution/v1",
            trial_id=request.trial_id,
            slot=slot,
            attempts=(attempt,),
            selected_attempt_id=attempt.attempt_id,
        )


def test_observation_factory_is_exercised_for_evaluated_attempt_contract() -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    spec = plan.experiment_specs[0]
    observation = observe_runtime_authority(spec)
    observation.verify(spec)


def test_retry_policy_rejects_timeout_relabelled_as_eligible() -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    slot = plan.expand_trial_slots()[0]
    spec = plan.experiment_specs[0]
    request = ControlledAttemptRequest(
        trial_id=digest("timeout-trial"),
        slot=slot,
        specification=spec,
        ordinal=1,
        predecessor_attempt_id=None,
        global_deadline_monotonic=100.0,
    )
    attempt = lifecycle_attempt(request, retry_eligible=True)
    payload = attempt.model_dump(mode="json", exclude={"attempt_id"})
    lifecycle = payload["lifecycle"]
    assert isinstance(lifecycle, dict)
    lifecycle["category"] = "timed_out"
    lifecycle["source_state"] = "timed-out"
    attempt = materialize_controlled_attempt(payload)

    with pytest.raises(ValueError, match="outside the frozen retry policy"):
        attempt.verify_authority(spec)
