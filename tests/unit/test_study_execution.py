from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from cernora import BatchAttemptResources

from cernora_reference_workflow.controlled_evaluation import materialize_repair_result
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptRequest,
    materialize_controlled_attempt,
    publish_controlled_attempt_artifact,
    verify_controlled_attempt_artifact,
)
from cernora_reference_workflow.controlled_experiment_spec import ControlledExperimentSpecV2
from cernora_reference_workflow.controlled_profile import evaluate_repair_result_package
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    ControlledTrialSlotV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.controlled_runtime import observe_runtime_authority
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority, load_visible_task
from cernora_reference_workflow.execution import (
    ControlledTrialResultManifest,
    initialize_execution,
    reload_execution,
    verify_execution_pack,
)
from cernora_reference_workflow.runner import _AttemptRequest, advance_repeat
from tests.unit.test_controlled_execution import lifecycle_attempt
from tests.unit.test_controlled_live_attempt import _spec
from tests.unit.test_controlled_run_plan import valid_payload as valid_run_plan_payload
from tests.unit.test_improvement_loop import _all_task_authorities, _final_plan, _manifest
from tests.unit.test_study_projection import study_payload_for_m4


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _evaluated_plan(task: ControlledTaskAuthority) -> ControlledRunPlanV2:
    specifications = (
        _spec(task, configuration_id="baseline", prompt="Repair the project."),
        _spec(
            task,
            configuration_id="candidate",
            prompt="Inspect the leading failure, then repair the project.",
        ),
    )
    payload = valid_run_plan_payload()
    payload["experiment_specs"] = [item.model_dump(mode="json") for item in specifications]
    payload["cases"] = [
        {
            "case_id": task.case.case_id,
            "case_version": task.case.case_version,
            "task_content_sha256": specifications[0].task.content_sha256,
        }
    ]
    payload["cells"] = [
        {
            "case_id": item.task.task_id,
            "configuration_id": item.configuration_id,
            "experiment_id": item.experiment_id,
        }
        for item in specifications
    ]
    payload["planned_trial_count"] = 6
    payload["worst_case_attempt_count"] = 12
    execution = payload["execution"]
    assert isinstance(execution, dict)
    execution["max_attempt_count"] = 12
    return materialize_controlled_run_plan(payload)


def _evaluated_attempt(
    request: ControlledAttemptRequest,
    *,
    task: ControlledTaskAuthority,
    evaluation_root: Path,
    passed: bool = False,
    tasks: tuple[ControlledTaskAuthority, ...] | None = None,
) -> ControlledAttempt:
    result = materialize_repair_result(
        {
            "schema_version": "cernora.reference.repair-result/v1",
            "case_id": task.case.case_id,
            "result_record_version": "agent.evaluator.result-record/v1",
            "test_authority_sha256": request.specification.test_runner.authority_sha256,
            "test_plan_sha256": request.specification.test_runner.test_plan_sha256,
            "test_source_sha256": request.specification.test_runner.test_source_sha256,
            "termination": "exited",
            "exit_code": 0 if passed else 1,
            "checks": [
                {
                    "check_id": "authoritative-verifier",
                    "failure_code": task.failure_code,
                    "passed": passed,
                }
            ],
            "allowed_paths": list(task.allowed_paths),
            "changed_paths": list(task.allowed_paths),
            "protected_paths": list(task.protected_paths),
            "protected_path_receipt": {
                "before_sha256": _digest("protected-tree"),
                "after_sha256": _digest("protected-tree"),
                "unchanged": True,
            },
        }
    )
    source_attempt_id = _digest(
        f"evaluated:{request.trial_id}:{request.ordinal}:{request.specification.experiment_id}"
    )
    package = evaluate_repair_result_package(
        task=task,
        tasks=tasks or (task,),
        result=result,
        source_attempt_id=source_attempt_id,
        output=evaluation_root / f"{request.trial_id}-{request.ordinal}",
    )
    observation = observe_runtime_authority(request.specification)
    return materialize_controlled_attempt(
        {
            "schema_version": "cernora.reference.controlled-attempt/v1",
            "trial_id": request.trial_id,
            "ordinal": request.ordinal,
            "predecessor_attempt_id": request.predecessor_attempt_id,
            "source_attempt_id": source_attempt_id,
            "source_manifest_sha256": _digest(f"manifest:{source_attempt_id}"),
            "retry_eligible": False,
            "resources": BatchAttemptResources(duration_milliseconds=1).model_dump(mode="json"),
            "runtime_observation": observation.model_dump(mode="json"),
            "repair_result": result.model_dump(mode="json"),
            "evaluation": package.model_dump(mode="python"),
            "lifecycle": None,
        }
    )


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


class FakeEvaluatedStudyAttemptAdapter:
    def __init__(
        self,
        task: ControlledTaskAuthority,
        evaluation_root: Path,
        *,
        passed: bool = False,
    ) -> None:
        self.task = task
        self.evaluation_root = evaluation_root
        self.evaluation_root.mkdir()
        self.passed = passed
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
        attempt = _evaluated_attempt(
            controlled_request,
            task=self.task,
            evaluation_root=self.evaluation_root,
            passed=self.passed,
        )
        publish_controlled_attempt_artifact(
            request.destination,
            attempt=attempt,
            specification=specification,
        )


class FakeRetryThenEvaluatedStudyAttemptAdapter(FakeEvaluatedStudyAttemptAdapter):
    def __call__(self, request: _AttemptRequest) -> None:
        if request.active_record.ordinal != 1:
            super().__call__(request)
            return
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
        attempt = lifecycle_attempt(controlled_request, retry_eligible=True)
        publish_controlled_attempt_artifact(
            request.destination,
            attempt=attempt,
            specification=specification,
        )


class FakeEvaluatedMatrixAdapter:
    def __init__(
        self,
        tasks: tuple[ControlledTaskAuthority, ...],
        evaluation_root: Path,
    ) -> None:
        self.tasks = tuple(sorted(tasks, key=lambda item: item.case.case_id))
        self.task_by_id = {item.case.case_id: item for item in self.tasks}
        self.evaluation_root = evaluation_root
        self.evaluation_root.mkdir()
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
        attempt = _evaluated_attempt(
            controlled_request,
            task=self.task_by_id[specification.task.task_id],
            evaluation_root=self.evaluation_root,
            passed=specification.configuration_id == "candidate",
            tasks=self.tasks,
        )
        publish_controlled_attempt_artifact(
            request.destination,
            attempt=attempt,
            specification=specification,
        )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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
    assert isinstance(stopped.state.trial_results[0], ControlledTrialResultManifest)
    assert stopped.state.trial_results[0].evaluation_status == "unavailable"
    assert len(stopped.state.trial_manifests) == 1
    assert stopped.state.trial_manifests[0].attempts[0].artifact_kind == "controlled-attempt"
    assert stopped.state.checkpoints[-1].status == "stopped"


@pytest.mark.parametrize(
    ("passed", "terminal_state", "reason"),
    (
        (False, "behavioral-failure", "authoritative-repair-failed"),
        (True, "completed", "authoritative-repair-passed"),
    ),
)
def test_repeat_runner_adopts_and_finalizes_one_strict_evaluated_v2_attempt(
    tmp_path: Path, passed: bool, terminal_state: str, reason: str
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    run_plan = _evaluated_plan(task)
    root = tmp_path / "execution"
    initialize_execution(root, run_plan, nonce="d" * 64)
    adapter = FakeEvaluatedStudyAttemptAdapter(
        task,
        tmp_path / "evaluations",
        passed=passed,
    )

    running = advance_repeat(root, adapter)

    assert running.status == "running"
    assert len(adapter.requests) == 1
    artifact = verify_controlled_attempt_artifact(adapter.requests[0].destination)
    assert artifact.attempt.evaluation is not None
    assert artifact.attempt.repair_result is not None
    assert artifact.terminal.state == terminal_state
    assert artifact.terminal.reason == reason

    stopped = advance_repeat(root, adapter, should_stop=lambda: True)

    assert stopped.status == "stopped"
    assert len(adapter.requests) == 1
    assert len(stopped.state.trial_results) == 1
    assert isinstance(stopped.state.trial_results[0], ControlledTrialResultManifest)
    assert stopped.state.trial_results[0].evaluation_status == "evaluated"
    assert len(stopped.state.trial_manifests) == 1
    assert stopped.state.trial_manifests[0].terminal_state == terminal_state


def test_evaluated_attempt_rejects_a_package_for_another_source_attempt(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    run_plan = _evaluated_plan(task)
    slot = run_plan.expand_trial_slots()[0]
    specification = next(
        item for item in run_plan.experiment_specs if item.experiment_id == slot.experiment_id
    )
    request = ControlledAttemptRequest(
        trial_id=_digest("source-binding-trial"),
        slot=slot,
        specification=specification,
        ordinal=1,
        predecessor_attempt_id=None,
        global_deadline_monotonic=0.0,
    )
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    attempt = _evaluated_attempt(
        request,
        task=task,
        evaluation_root=evaluation_root,
    )
    payload = attempt.model_dump(mode="python", exclude={"attempt_id"})
    payload["source_attempt_id"] = _digest("another-source-attempt")

    with pytest.raises(ValueError, match="does not bind the source Attempt"):
        materialize_controlled_attempt(payload)


def test_advance_adopts_evaluated_artifact_after_callback_crash_without_reexecution(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    run_plan = _evaluated_plan(task)
    root = tmp_path / "execution"
    initialize_execution(root, run_plan, nonce="e" * 64)
    publisher = FakeEvaluatedStudyAttemptAdapter(task, tmp_path / "evaluations")

    def publish_then_crash(request: _AttemptRequest) -> None:
        publisher(request)
        raise RuntimeError("simulated crash after evaluated artifact publication")

    with pytest.raises(RuntimeError, match="after evaluated artifact publication"):
        advance_repeat(root, publish_then_crash)

    interrupted = reload_execution(root)
    assert len(interrupted.active_attempts) == 1
    assert not interrupted.trial_results
    assert not interrupted.trial_manifests
    artifact = verify_controlled_attempt_artifact(publisher.requests[0].destination)
    assert artifact.attempt.evaluation is not None

    def must_not_execute(request: _AttemptRequest) -> None:
        raise AssertionError(f"adopted evaluated Attempt was rerun: {request.destination}")

    stopped = advance_repeat(root, must_not_execute, should_stop=lambda: True)

    assert stopped.status == "stopped"
    assert len(stopped.state.active_attempts) == 1
    assert len(stopped.state.trial_manifests) == 1
    assert stopped.state.trial_manifests[0].terminal_state == "behavioral-failure"


def test_advance_preserves_one_authorized_retry_before_evaluated_terminal(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    run_plan = _evaluated_plan(task)
    root = tmp_path / "execution"
    initialize_execution(root, run_plan, nonce="f" * 64)
    adapter = FakeRetryThenEvaluatedStudyAttemptAdapter(task, tmp_path / "evaluations")
    delays: list[float] = []

    first = advance_repeat(root, adapter)

    assert first.status == "running"
    first_boundary = reload_execution(root)
    assert len(first_boundary.active_attempts) == 1
    first_artifact = verify_controlled_attempt_artifact(adapter.requests[0].destination)
    assert first_artifact.terminal.retry_eligible

    second = advance_repeat(root, adapter, sleeper=delays.append)

    assert second.status == "running"
    assert delays == [10]
    second_boundary = reload_execution(root)
    assert len(second_boundary.active_attempts) == 2
    second_artifact = verify_controlled_attempt_artifact(adapter.requests[1].destination)
    assert second_artifact.attempt.evaluation is not None
    assert second_artifact.attempt.predecessor_attempt_id == first_artifact.attempt.attempt_id
    assert not second_artifact.terminal.retry_eligible

    stopped = advance_repeat(root, adapter, should_stop=lambda: True)

    assert stopped.status == "stopped"
    assert len(adapter.requests) == 2
    first_binding, second_binding = stopped.state.trial_manifests[0].attempts
    assert second_binding.predecessor_attempt_id == first_binding.attempt_id
    assert stopped.state.trial_manifests[0].terminal_state == "behavioral-failure"


def test_public_advance_closes_the_full_offline_m4_matrix_and_byte_stable_pack(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    tasks = tuple(sorted(_all_task_authorities(manifest), key=lambda item: item.case.case_id))
    run_plan = _final_plan(manifest, task_authorities=tasks)
    root = tmp_path / "execution"
    first_pack = tmp_path / "execution-pack-a"
    second_pack = tmp_path / "execution-pack-b"
    initialize_execution(root, run_plan, nonce="9" * 64)
    adapter = FakeEvaluatedMatrixAdapter(tasks, tmp_path / "evaluations")

    outcome = None
    for _ in range(55):
        before = len(adapter.requests)
        outcome = advance_repeat(root, adapter, pack_root=first_pack)
        assert len(adapter.requests) - before <= 1
        assert reload_execution(root) == outcome.state
        if outcome.status == "completed":
            break

    assert outcome is not None and outcome.status == "completed"
    assert len(adapter.requests) == 54
    assert tuple(request.trial.slot for request in adapter.requests) == (
        run_plan.expand_trial_slots()
    )
    assert len(outcome.state.active_attempts) == 54
    assert len(outcome.state.trial_results) == 54
    assert len(outcome.state.trial_manifests) == 54
    assert outcome.state.diagnostic is not None
    assert {item.result_status for item in outcome.state.diagnostic.trials} == {"evaluated"}
    assert verify_execution_pack(first_pack).execution_id == outcome.state.record.execution_id

    def must_not_execute(request: _AttemptRequest) -> None:
        raise AssertionError(f"completed matrix attempted new work: {request.destination}")

    rebuilt = advance_repeat(root, must_not_execute, pack_root=second_pack)

    assert rebuilt.status == "completed"
    assert verify_execution_pack(second_pack).pack_id == verify_execution_pack(first_pack).pack_id
    assert _tree_bytes(second_pack) == _tree_bytes(first_pack)
