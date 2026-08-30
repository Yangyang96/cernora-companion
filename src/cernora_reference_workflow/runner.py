"""Sequential Priority 4 Milestone 1 Repeat Runner.

The runner owns ordering, retry, budget, and recovery policy.  Runtime-specific
work remains behind one injected per-attempt callable; that callable must
atomically publish exactly one P3 Attempt artifact at the supplied destination.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from cernora_reference_workflow.attempt_record import verify_preterminal_attempt
from cernora_reference_workflow.common import ContractError, load_json_file, sha256_file
from cernora_reference_workflow.controlled_execution import verify_controlled_attempt_artifact
from cernora_reference_workflow.controlled_experiment_spec import ControlledExperimentSpecV2
from cernora_reference_workflow.execution import (
    ActiveAttemptRecord,
    ExecutionState,
    ExecutionTrialSlot,
    initialize_execution,
    publish_checkpoint,
    publish_execution_manifest,
    publish_execution_pack,
    publish_trial_manifest,
    publish_trial_result,
    reload_execution,
    reload_execution_for_reconciliation,
    start_attempt,
    verify_execution_pack,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.export import verify_completed_export
from cernora_reference_workflow.lifecycle import TerminalRecord
from cernora_reference_workflow.offline import evaluate_frozen_export
from cernora_reference_workflow.report_builder import (
    build_run_report,
    build_unavailable_run_report,
)
from cernora_reference_workflow.run_plan import RunPlan

RunnerStatus = Literal["running", "stopped", "budget-exhausted", "completed"]
Clock = Callable[[], float]
WallClock = Callable[[], float]
Sleeper = Callable[[float], None]
StopPredicate = Callable[[], bool]


@dataclass(frozen=True)
class _AttemptRequest:
    """Frozen private handoff to the one qualified per-attempt executor."""

    execution_root: Path
    destination: Path
    trial: ExecutionTrialSlot
    specification: ExperimentSpec | ControlledExperimentSpecV2
    active_record: ActiveAttemptRecord


class _AttemptExecutor(Protocol):
    def __call__(self, request: _AttemptRequest) -> None:
        """Publish one completed-export or preterminal Attempt directory."""


ReconciliationResult = Literal["published", "active", "absent"]


@runtime_checkable
class AttemptRuntimeAdapter(Protocol):
    """Narrow true-external seam for invocation and interrupted-process reconciliation."""

    def execute(self, request: _AttemptRequest) -> None:
        """Publish one terminal Attempt artifact or raise without claiming a result."""

    def reconcile(self, request: _AttemptRequest) -> ReconciliationResult:
        """Publish recovered terminal evidence or report the invocation's observed status."""


AttemptExecutor = _AttemptExecutor | AttemptRuntimeAdapter


class AmbiguousActiveAttempt(ContractError):
    """An active Attempt remains unsafe to repeat after Runtime reconciliation."""

    def __init__(
        self,
        active_record: ActiveAttemptRecord,
        reconciliation: Literal["active", "absent"],
    ) -> None:
        self.active_record = active_record
        self.reconciliation = reconciliation
        super().__init__(f"active Attempt remains {reconciliation} after reconciliation")


@dataclass(frozen=True)
class RunnerOutcome:
    status: RunnerStatus
    state: ExecutionState
    pack_root: Path | None


@dataclass(frozen=True)
class _AttemptFacts:
    terminal: TerminalRecord
    source_trial_id: str
    manifest_sha256: str
    kind: Literal["completed-export", "preterminal", "controlled-attempt"]


def _attempt_path(root: Path, trial_id: str, ordinal: int) -> Path:
    return root / "attempts" / trial_id / f"{ordinal:04d}"


def _attempt_request(
    root: Path, state: ExecutionState, active: ActiveAttemptRecord
) -> _AttemptRequest:
    trial = next(item for item in state.trial_slots.slots if item.trial_id == active.trial_id)
    return _AttemptRequest(
        execution_root=root,
        destination=_attempt_path(root, active.trial_id, active.ordinal),
        trial=trial,
        specification=_specification(state, trial),
        active_record=active,
    )


def _execute_attempt(executor: AttemptExecutor, request: _AttemptRequest) -> None:
    if isinstance(executor, AttemptRuntimeAdapter):
        executor.execute(request)
    else:
        executor(request)


def _reconcile_interrupted(root: Path, executor: AttemptExecutor) -> None:
    if not isinstance(executor, AttemptRuntimeAdapter):
        return
    state = reload_execution_for_reconciliation(root)
    ambiguous = tuple(
        item
        for item in state.active_attempts
        if not _attempt_path(root, item.trial_id, item.ordinal).exists()
    )
    if not ambiguous:
        return
    if len(ambiguous) != 1:
        raise ContractError("Execution contains multiple ambiguous active Attempts")
    active = ambiguous[0]
    reconciliation = executor.reconcile(_attempt_request(root, state, active))
    if reconciliation != "published":
        raise AmbiguousActiveAttempt(active, reconciliation)


def _attempt_facts(path: Path) -> _AttemptFacts:
    manifest_payload = load_json_file(path / "manifest.json")
    terminal_payload = load_json_file(path / "terminal.json")
    if not isinstance(manifest_payload, dict) or not isinstance(terminal_payload, dict):
        raise ContractError("Attempt artifact files must contain JSON objects")
    schema_version = manifest_payload.get("schema_version")
    if schema_version == "cernora.reference.completed-export/v1":
        completed = verify_completed_export(path)
        source_trial_id = completed.source_trial_id
        kind: Literal["completed-export", "preterminal", "controlled-attempt"] = "completed-export"
    elif schema_version == "cernora.reference.preterminal-attempt/v1":
        preterminal = verify_preterminal_attempt(path)
        source_trial_id = preterminal.source_trial_id
        kind = "preterminal"
    elif schema_version == "cernora.reference.controlled-attempt-artifact/v1":
        controlled = verify_controlled_attempt_artifact(path)
        source_trial_id = controlled.attempt.trial_id
        kind = "controlled-attempt"
    else:
        raise ContractError("Attempt executor published an unknown artifact contract")
    terminal = TerminalRecord.model_validate(terminal_payload)
    return _AttemptFacts(
        terminal=terminal,
        source_trial_id=source_trial_id,
        manifest_sha256=sha256_file(path / "manifest.json"),
        kind=kind,
    )


def _specification(
    state: ExecutionState, trial: ExecutionTrialSlot
) -> ExperimentSpec | ControlledExperimentSpecV2:
    return next(
        item
        for item in state.run_plan.experiment_specs
        if item.experiment_id == trial.slot.experiment_id
    )


def _trial_attempts(root: Path, state: ExecutionState, trial_id: str) -> tuple[_AttemptFacts, ...]:
    records = tuple(item for item in state.active_attempts if item.trial_id == trial_id)
    return tuple(_attempt_facts(_attempt_path(root, trial_id, item.ordinal)) for item in records)


@dataclass(frozen=True)
class _ElapsedTracker:
    base_milliseconds: int
    monotonic_started: float
    wall_started_milliseconds: int
    clock: Clock
    wall_clock: WallClock

    def current(self) -> int:
        monotonic_delta = int((self.clock() - self.monotonic_started) * 1000)
        wall_now = int(self.wall_clock() * 1000)
        wall_delta = wall_now - self.wall_started_milliseconds
        if monotonic_delta < 0 or wall_delta < 0:
            raise ContractError("Execution clocks moved backward")
        return self.base_milliseconds + max(monotonic_delta, wall_delta)


def _recover_elapsed_base(state: ExecutionState, wall_now: int) -> int:
    base = state.checkpoints[-1].elapsed_milliseconds if state.checkpoints else 0
    checkpointed = (
        {(item.trial_id, item.ordinal) for item in state.checkpoints[-1].active_attempts}
        if state.checkpoints
        else set()
    )
    for active in state.active_attempts:
        if (active.trial_id, active.ordinal) in checkpointed:
            continue
        if wall_now < active.started_unix_milliseconds:
            raise ContractError("wall clock moved backward since an active Attempt started")
        base = max(
            base,
            active.elapsed_before_attempt_milliseconds
            + wall_now
            - active.started_unix_milliseconds,
        )
    return base


def _finalize_trial(root: Path, trial: ExecutionTrialSlot) -> None:
    state = reload_execution(root)
    if any(item.trial_id == trial.trial_id for item in state.trial_manifests):
        return
    if any(item.trial_id == trial.trial_id for item in state.trial_results):
        publish_trial_manifest(root, trial.trial_id)
        return

    spec = _specification(state, trial)
    if not isinstance(spec, ExperimentSpec):
        raise ContractError("controlled V2 Attempt finalization is not yet implemented")
    attempts = _trial_attempts(root, state, trial.trial_id)
    if not attempts:
        raise ContractError("Trial cannot be finalized without a terminal Attempt")
    selected = attempts[-1]
    if selected.terminal.retry_eligible and len(attempts) <= spec.retry.max_retries:
        raise ContractError("retry-eligible Trial still requires its frozen retry")
    report_attempts = tuple(
        (item.terminal, item.source_trial_id, item.manifest_sha256) for item in attempts
    )
    if selected.kind == "preterminal":
        report = build_unavailable_run_report(spec=spec, attempts=report_attempts)
        publish_trial_result(root, trial.trial_id, report=report)
    else:
        export_root = _attempt_path(root, trial.trial_id, len(attempts))
        with tempfile.TemporaryDirectory(prefix="cernora-offline-", dir=root.parent) as temporary:
            offline_root = Path(temporary) / "offline-evaluation"
            evaluation = evaluate_frozen_export(
                spec=spec,
                export_root=export_root,
                output_root=offline_root,
            )
            report = build_run_report(
                spec=spec,
                export_root=export_root,
                evaluation=evaluation,
                portable_spec_path=f"specs/{spec.experiment_id}.json",
                portable_export_path=(f"attempts/{trial.trial_id}/{len(attempts):04d}"),
                portable_bundle_path=(
                    f"results/{trial.trial_id}/offline-evaluation/adapted/bundle.json"
                ),
                portable_evaluation_path=(f"results/{trial.trial_id}/offline-evaluation/evaluated"),
                prior_attempts=report_attempts[:-1],
            )
            publish_trial_result(
                root,
                trial.trial_id,
                report=report,
                offline_evaluation_root=offline_root,
            )
    publish_trial_manifest(root, trial.trial_id)


def _publish_pack(root: Path, destination: Path) -> Path:
    if destination.exists():
        pack = verify_execution_pack(destination)
        state = reload_execution(root)
        if (
            pack.execution_id != state.record.execution_id
            or pack.run_plan_id != state.run_plan.run_plan_id
        ):
            raise ContractError("preexisting Execution Pack belongs to another Execution")
    else:
        publish_execution_pack(root, destination)
    return destination


def _finish_completed(root: Path, pack_root: Path) -> RunnerOutcome:
    state = reload_execution(root)
    if not state.checkpoints or state.checkpoints[-1].status != "completed":
        raise ContractError("completed Execution is missing its terminal checkpoint")
    if state.manifest is None:
        publish_execution_manifest(root)
    packed = _publish_pack(root, pack_root)
    return RunnerOutcome(status="completed", state=reload_execution(root), pack_root=packed)


def _drive(
    root: Path,
    executor: AttemptExecutor,
    *,
    pack_root: Path,
    should_stop: StopPredicate | None,
    clock: Clock,
    wall_clock: WallClock,
    sleeper: Sleeper,
    reject_terminal_budget: bool,
    return_after_attempt: bool = False,
) -> RunnerOutcome:
    _reconcile_interrupted(root, executor)
    state = reload_execution(root)
    if state.manifest is not None:
        return _publish_completed_pack(root, pack_root)
    if state.checkpoints and state.checkpoints[-1].status == "budget-exhausted":
        if reject_terminal_budget:
            raise ContractError("budget-exhausted Execution cannot be resumed")
        return RunnerOutcome(status="budget-exhausted", state=state, pack_root=None)
    if state.checkpoints and state.checkpoints[-1].status == "completed":
        return _finish_completed(root, pack_root)

    wall_started = int(wall_clock() * 1000)
    tracker = _ElapsedTracker(
        base_milliseconds=_recover_elapsed_base(state, wall_started),
        monotonic_started=clock(),
        wall_started_milliseconds=wall_started,
        clock=clock,
        wall_clock=wall_clock,
    )
    wall_budget = state.run_plan.execution.max_total_wall_time_seconds * 1000

    while True:
        state = reload_execution(root)
        completed = {item.trial_id for item in state.trial_manifests}
        if state.adopted_trial_ids:
            adopted_elapsed = tracker.current()
            if adopted_elapsed >= wall_budget:
                publish_checkpoint(
                    root,
                    status="budget-exhausted",
                    elapsed_milliseconds=adopted_elapsed,
                )
                return RunnerOutcome(
                    status="budget-exhausted", state=reload_execution(root), pack_root=None
                )
            publish_checkpoint(
                root,
                elapsed_milliseconds=adopted_elapsed,
            )
            continue
        next_trial = next(
            (item for item in state.trial_slots.slots if item.trial_id not in completed), None
        )
        elapsed = tracker.current()
        if next_trial is None:
            if elapsed >= wall_budget:
                publish_checkpoint(root, status="budget-exhausted", elapsed_milliseconds=elapsed)
                return RunnerOutcome(
                    status="budget-exhausted", state=reload_execution(root), pack_root=None
                )
            publish_checkpoint(root, status="completed", elapsed_milliseconds=elapsed)
            return _finish_completed(root, pack_root)

        records = tuple(
            item for item in state.active_attempts if item.trial_id == next_trial.trial_id
        )
        facts = _trial_attempts(root, state, next_trial.trial_id) if records else ()
        if facts:
            if elapsed >= wall_budget:
                publish_checkpoint(root, status="budget-exhausted", elapsed_milliseconds=elapsed)
                return RunnerOutcome(
                    status="budget-exhausted", state=reload_execution(root), pack_root=None
                )
            spec = _specification(state, next_trial)
            selected = facts[-1]
            retry_required = (
                selected.terminal.retry_eligible and len(facts) <= spec.retry.max_retries
            )
            if not retry_required:
                _finalize_trial(root, next_trial)
                finalized_elapsed = tracker.current()
                if finalized_elapsed >= wall_budget:
                    publish_checkpoint(
                        root,
                        status="budget-exhausted",
                        elapsed_milliseconds=finalized_elapsed,
                    )
                    return RunnerOutcome(
                        status="budget-exhausted",
                        state=reload_execution(root),
                        pack_root=None,
                    )
                publish_checkpoint(
                    root,
                    elapsed_milliseconds=finalized_elapsed,
                )
                continue

        # Operator stops and budgets are observed only at a Trial boundary or
        # between terminal retry Attempts, never while an executor is active.
        if not records and should_stop is not None and should_stop():
            publish_checkpoint(root, status="stopped", elapsed_milliseconds=elapsed)
            return RunnerOutcome(status="stopped", state=reload_execution(root), pack_root=None)
        attempt_count = len(state.active_attempts)
        if attempt_count >= state.run_plan.execution.max_attempt_count or elapsed >= wall_budget:
            publish_checkpoint(root, status="budget-exhausted", elapsed_milliseconds=elapsed)
            return RunnerOutcome(
                status="budget-exhausted", state=reload_execution(root), pack_root=None
            )

        if facts:
            retry_delay = _specification(state, next_trial).retry.delay_seconds
            sleeper(retry_delay)
            elapsed = tracker.current()
            if elapsed >= wall_budget:
                publish_checkpoint(root, status="budget-exhausted", elapsed_milliseconds=elapsed)
                return RunnerOutcome(
                    status="budget-exhausted", state=reload_execution(root), pack_root=None
                )

        predecessor = facts[-1].terminal.attempt_id if facts else None
        started_unix = int(wall_clock() * 1000)
        active = start_attempt(
            root,
            next_trial.trial_id,
            predecessor_attempt_id=predecessor,
            elapsed_before_attempt_milliseconds=elapsed,
            started_unix_milliseconds=started_unix,
        )
        _execute_attempt(
            executor,
            _AttemptRequest(
                execution_root=root,
                destination=_attempt_path(root, next_trial.trial_id, active.ordinal),
                trial=next_trial,
                specification=_specification(state, next_trial),
                active_record=active,
            ),
        )
        # Strict reload proves that the executor atomically published the exact
        # artifact bound by the active record. An absent artifact stays ambiguous.
        reloaded = reload_execution(root)
        if return_after_attempt:
            return RunnerOutcome(status="running", state=reloaded, pack_root=None)


def _publish_completed_pack(root: Path, pack_root: Path) -> RunnerOutcome:
    packed = _publish_pack(root, pack_root)
    return RunnerOutcome(status="completed", state=reload_execution(root), pack_root=packed)


def run_repeat(
    destination: Path,
    run_plan: RunPlan,
    executor: AttemptExecutor,
    *,
    nonce: str | None = None,
    pack_root: Path | None = None,
    should_stop: StopPredicate | None = None,
    clock: Clock = time.monotonic,
    wall_clock: WallClock = time.time,
    sleeper: Sleeper = time.sleep,
) -> RunnerOutcome:
    """Initialize and drive a new deterministic Repeat Runner Execution."""

    initialize_execution(destination, run_plan, nonce=nonce)
    return _drive(
        destination,
        executor,
        pack_root=pack_root or destination.with_name(f"{destination.name}.pack"),
        should_stop=should_stop,
        clock=clock,
        wall_clock=wall_clock,
        sleeper=sleeper,
        reject_terminal_budget=False,
    )


def advance_repeat(
    root: Path,
    executor: AttemptExecutor,
    *,
    pack_root: Path | None = None,
    should_stop: StopPredicate | None = None,
    clock: Clock = time.monotonic,
    wall_clock: WallClock = time.time,
    sleeper: Sleeper = time.sleep,
) -> RunnerOutcome:
    """Advance one existing Repeat Execution by at most one external Attempt."""

    return _drive(
        root,
        executor,
        pack_root=pack_root or root.with_name(f"{root.name}.pack"),
        should_stop=should_stop,
        clock=clock,
        wall_clock=wall_clock,
        sleeper=sleeper,
        reject_terminal_budget=False,
        return_after_attempt=True,
    )


def resume_repeat(
    root: Path,
    executor: AttemptExecutor,
    *,
    pack_root: Path | None = None,
    should_stop: StopPredicate | None = None,
    clock: Clock = time.monotonic,
    wall_clock: WallClock = time.time,
    sleeper: Sleeper = time.sleep,
) -> RunnerOutcome:
    """Strictly resume a stopped or crash-interrupted Repeat Runner Execution."""

    return _drive(
        root,
        executor,
        pack_root=pack_root or root.with_name(f"{root.name}.pack"),
        should_stop=should_stop,
        clock=clock,
        wall_clock=wall_clock,
        sleeper=sleeper,
        reject_terminal_budget=True,
    )


__all__ = [
    "AmbiguousActiveAttempt",
    "AttemptRuntimeAdapter",
    "RunnerOutcome",
    "advance_repeat",
    "resume_repeat",
    "run_repeat",
]
