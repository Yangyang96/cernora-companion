from __future__ import annotations

from pathlib import Path

from cernora_reference_workflow.controlled_execution import (
    ControlledAttemptRequest,
    publish_controlled_attempt_artifact,
    verify_controlled_attempt_artifact,
)
from cernora_reference_workflow.controlled_experiment_spec import ControlledExperimentSpecV2
from cernora_reference_workflow.controlled_run_plan import ControlledTrialSlotV2
from cernora_reference_workflow.execution import initialize_execution
from cernora_reference_workflow.runner import _AttemptRequest, advance_repeat
from tests.unit.test_controlled_execution import lifecycle_attempt
from tests.unit.test_study_projection import study_payload_for_m4


class FakeStudyAttemptAdapter:
    def __init__(self) -> None:
        self.requests: list[_AttemptRequest] = []

    def __call__(self, request: _AttemptRequest) -> None:
        self.requests.append(request)
        specification = request.specification
        assert isinstance(specification, ControlledExperimentSpecV2)
        assert isinstance(request.trial.slot, ControlledTrialSlotV2)
        controlled_request = ControlledAttemptRequest(
            trial_id=request.trial.trial_id,
            slot=request.trial.slot,
            specification=specification,
            ordinal=request.active_record.ordinal,
            predecessor_attempt_id=request.active_record.predecessor_attempt_id,
            global_deadline_monotonic=0.0,
        )
        attempt = lifecycle_attempt(controlled_request)
        publish_controlled_attempt_artifact(
            request.destination,
            attempt=attempt,
            specification=specification,
        )


def test_repeat_runner_adopts_one_v2_attempt_without_the_legacy_m4_store(
    tmp_path: Path,
) -> None:
    _, run_plan = study_payload_for_m4()
    root = tmp_path / "execution"
    initialize_execution(root, run_plan, nonce="b" * 64)
    adapter = FakeStudyAttemptAdapter()

    outcome = advance_repeat(root, adapter)

    assert outcome.status == "running"
    assert len(adapter.requests) == 1
    assert len(outcome.state.active_attempts) == 1
    artifact = verify_controlled_attempt_artifact(adapter.requests[0].destination)
    assert artifact.attempt.trial_id == outcome.state.active_attempts[0].trial_id
    assert artifact.manifest.experiment_id == adapter.requests[0].specification.experiment_id


def test_repeat_runner_finalizes_a_v2_lifecycle_trial_before_stopping(
    tmp_path: Path,
) -> None:
    _, run_plan = study_payload_for_m4()
    root = tmp_path / "execution"
    initialize_execution(root, run_plan, nonce="c" * 64)
    adapter = FakeStudyAttemptAdapter()

    advance_repeat(root, adapter)
    stopped = advance_repeat(root, adapter, should_stop=lambda: True)

    assert stopped.status == "stopped"
    assert len(adapter.requests) == 1
    assert len(stopped.state.trial_results) == 1
    assert len(stopped.state.trial_manifests) == 1
    assert stopped.state.trial_manifests[0].attempts[0].artifact_kind == "controlled-attempt"
    assert stopped.state.checkpoints[-1].status == "stopped"
