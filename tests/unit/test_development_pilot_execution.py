from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptRequest,
)
from cernora_reference_workflow.development_agent_pilot import (
    PILOT_CASE_IDS,
    DevelopmentAgentPilotPlan,
    build_development_agent_pilot_plan,
    load_development_pilot_corpus,
    materialize_development_pilot_image_set,
)
from cernora_reference_workflow.development_pilot_execution import (
    AmbiguousDevelopmentPilotAttempt,
    inspect_development_pilot_execution,
    prepare_development_pilot_execution,
    step_development_pilot_execution,
    summarize_development_pilot_execution,
)
from tests.unit.test_controlled_execution import lifecycle_attempt
from tests.unit.test_study_execution import _evaluated_attempt

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "examples" / "priority4-development-pilot"
FREE = 20 * 1024**3


def _plan() -> DevelopmentAgentPilotPlan:
    corpus = load_development_pilot_corpus(CORPUS)
    images = materialize_development_pilot_image_set(
        build_base_image="cernora-reference/codex-runtime@sha256:" + "a" * 64,
        images={
            case_id: f"cernora-reference/p4-pilot-{case_id}@sha256:{index:064x}"
            for index, case_id in enumerate(PILOT_CASE_IDS, start=1)
        },
    )
    return build_development_agent_pilot_plan(corpus=corpus, images=images)


class EvaluatedExecutor:
    def __init__(
        self,
        plan: DevelopmentAgentPilotPlan,
        evaluation_root: Path,
        *,
        failing_cases: frozenset[str] = frozenset(),
    ) -> None:
        self.plan = plan
        self.evaluation_root = evaluation_root
        self.evaluation_root.mkdir()
        self.failing_cases = failing_cases
        self.requests: list[ControlledAttemptRequest] = []

    @property
    def enforces_hard_deadline(self) -> bool:
        return True

    def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
        self.requests.append(request)
        task = next(
            item for item in self.plan.corpus.tasks if item.case.case_id == request.slot.case_id
        )
        return _evaluated_attempt(
            request,
            task=task,
            tasks=self.plan.corpus.tasks,
            evaluation_root=self.evaluation_root,
            passed=task.case.case_id not in self.failing_cases,
        )


class CrashingExecutor:
    def __init__(self) -> None:
        self.requests: list[ControlledAttemptRequest] = []

    @property
    def enforces_hard_deadline(self) -> bool:
        return True

    def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
        self.requests.append(request)
        raise RuntimeError("simulated process loss after durable claim")


class LifecycleThenEvaluatedExecutor(EvaluatedExecutor):
    def __init__(
        self,
        plan: DevelopmentAgentPilotPlan,
        evaluation_root: Path,
        *,
        retry_first: bool,
    ) -> None:
        super().__init__(plan, evaluation_root)
        self.retry_first = retry_first

    def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
        if request.slot.slot_index == 1 and request.ordinal == 1:
            self.requests.append(request)
            return lifecycle_attempt(request, retry_eligible=self.retry_first)
        return super().__call__(request)


def _prepare(plan: DevelopmentAgentPilotPlan, custody: Path) -> None:
    result = prepare_development_pilot_execution(
        plan,
        custody,
        nonce="f" * 64,
        wall_clock=lambda: 1000.0,
        disk_free=lambda _: FREE,
    )
    assert result.status == "prepared"


def test_each_step_claims_at_most_one_attempt_and_all_passes_stop_no_candidate(
    tmp_path: Path,
) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    executor = EvaluatedExecutor(plan, tmp_path / "evaluations")

    for index in range(6):
        result = step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            disk_free=lambda _: FREE,
        )
        assert len(executor.requests) == index + 1

    assert result.status == "completed"
    state = inspect_development_pilot_execution(custody)
    assert state.outcome is not None
    assert state.outcome.status == "no-candidate"
    assert state.outcome.leading_failure_code is None
    assert len(state.outcome.observations) == 6
    assert all(item.agent_outcome == "pass" for item in state.outcome.observations)
    assert state.attempt_count == 6


def test_prepared_custody_is_not_reported_as_running(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)

    summary = summarize_development_pilot_execution(custody)

    assert summary.status == "prepared"
    assert summary.attempt_count == 0
    assert summary.completed_trial_count == 0


def test_real_evaluated_failure_is_selected_without_heldout_or_fabrication(
    tmp_path: Path,
) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    failing = frozenset({PILOT_CASE_IDS[0]})
    executor = EvaluatedExecutor(plan, tmp_path / "evaluations", failing_cases=failing)

    for _ in range(6):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            disk_free=lambda _: FREE,
        )

    outcome = inspect_development_pilot_execution(custody).outcome
    assert outcome is not None
    assert outcome.status == "candidate-eligible"
    assert outcome.leading_failure_code == "json_pointer_escape_order_v1"
    failures = [item for item in outcome.observations if item.agent_outcome == "behavioral-failure"]
    assert len(failures) == 1
    assert failures[0].case_id == PILOT_CASE_IDS[0]
    assert failures[0].source == "agent-pilot"


def test_crash_after_claim_is_ambiguous_and_never_retried(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    executor = CrashingExecutor()

    with pytest.raises(RuntimeError, match="simulated process loss"):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            disk_free=lambda _: FREE,
        )
    state = inspect_development_pilot_execution(custody)
    assert state.ambiguous_claim is not None
    assert state.attempt_count == 0

    with pytest.raises(AmbiguousDevelopmentPilotAttempt):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            disk_free=lambda _: FREE,
        )
    assert len(executor.requests) == 1


def test_wrong_plan_acceptance_fails_before_executor_call(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    executor = CrashingExecutor()

    with pytest.raises(ContractError, match="acceptance"):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id="0" * 64,
            disk_free=lambda _: FREE,
        )
    assert executor.requests == []


def test_final_infrastructure_attempt_makes_complete_pilot_inconclusive(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    executor = LifecycleThenEvaluatedExecutor(
        plan,
        tmp_path / "evaluations",
        retry_first=False,
    )

    for _ in range(6):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            disk_free=lambda _: FREE,
        )

    outcome = inspect_development_pilot_execution(custody).outcome
    assert outcome is not None
    assert outcome.status == "inconclusive"
    assert outcome.leading_failure_code is None
    assert len(outcome.observations) == 5


def test_retry_stays_inside_first_trial_and_uses_frozen_delay(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    executor = LifecycleThenEvaluatedExecutor(
        plan,
        tmp_path / "evaluations",
        retry_first=True,
    )
    sleeps: list[float] = []

    for _ in range(7):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            sleeper=sleeps.append,
            disk_free=lambda _: FREE,
        )

    state = inspect_development_pilot_execution(custody)
    assert state.outcome is not None
    assert state.outcome.status == "no-candidate"
    assert state.attempt_count == 7
    assert len(state.attempts_by_slot[0]) == 2
    assert sleeps == [10.0]
