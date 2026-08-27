"""Append-only atomic persistence and crash adoption for M4 execution."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    load_json_bytes,
    read_regular_file_bytes,
)
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledExecutionResult,
    ControlledTrialExecution,
)
from cernora_reference_workflow.controlled_experiment_spec import Digest, StrictV2Contract
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.publication import atomic_publish_directory


class StoredExecutionRecord(StrictV2Contract):
    schema_version: Literal["cernora.reference.stored-controlled-execution/v1"]
    execution_id: Digest
    run_plan_id: Digest
    nonce: Digest
    started_unix_milliseconds: Annotated[int, Field(ge=0)]
    wall_budget_milliseconds: Annotated[int, Field(gt=0)]
    attempt_budget: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def identity(self) -> Self:
        expected = canonical_content_id(
            {"nonce": self.nonce, "run_plan_id": self.run_plan_id}, excluded=frozenset()
        )
        if self.execution_id != expected:
            raise ValueError("stored execution identity mismatch")
        return self


class StoredActiveAttempt(StrictV2Contract):
    schema_version: Literal["cernora.reference.stored-active-attempt/v1"]
    active_id: Digest
    execution_id: Digest
    trial_id: Digest
    trial_slot_id: Digest
    ordinal: Annotated[int, Field(gt=0)]
    predecessor_attempt_id: Digest | None
    elapsed_before_attempt_milliseconds: Annotated[int, Field(ge=0)]
    started_unix_milliseconds: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"active_id"})
        )
        if self.active_id != expected:
            raise ValueError("stored active Attempt identity mismatch")
        return self


class StoredCheckpoint(StrictV2Contract):
    schema_version: Literal["cernora.reference.controlled-checkpoint/v1"]
    checkpoint_id: Digest
    execution_id: Digest
    sequence: Annotated[int, Field(gt=0)]
    previous_checkpoint_id: Digest | None
    status: Literal["running", "safe-stopped", "completed"]
    elapsed_milliseconds: Annotated[int, Field(ge=0)]
    attempt_count: Annotated[int, Field(ge=0)]
    completed_trial_ids: tuple[Digest, ...]
    observed_unix_milliseconds: Annotated[int, Field(ge=0)]

    @field_validator("completed_trial_ids", mode="before")
    @classmethod
    def tuple_completed_trials(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def identity_and_trials(self) -> Self:
        if len(self.completed_trial_ids) != len(set(self.completed_trial_ids)):
            raise ValueError("checkpoint completed Trials must be unique")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"checkpoint_id"})
        )
        if self.checkpoint_id != expected:
            raise ValueError("controlled checkpoint identity mismatch")
        return self


@dataclass(frozen=True)
class ControlledStoreState:
    record: StoredExecutionRecord
    plan: ControlledRunPlanV2
    active: tuple[StoredActiveAttempt, ...]
    attempts: tuple[ControlledAttempt, ...]
    checkpoints: tuple[StoredCheckpoint, ...]
    adopted_attempt_ids: tuple[str, ...]

    @property
    def elapsed_milliseconds(self) -> int:
        return self.checkpoints[-1].elapsed_milliseconds if self.checkpoints else 0


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ContractError(f"append-only execution path already exists: {path.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _model(path: Path, model: type[StrictV2Contract]) -> StrictV2Contract:
    raw = read_regular_file_bytes(path)
    payload = load_json_bytes(raw)
    if not isinstance(payload, dict):
        raise ContractError(f"stored {path.name} is not one JSON object")
    value = model.model_validate(payload)
    if raw != canonical_json_bytes(value.model_dump(mode="json")):
        raise ContractError(f"stored {path.name} is not canonical JSON")
    return value


class ControlledExecutionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def initialize(
        self,
        plan: ControlledRunPlanV2,
        *,
        nonce: str,
        started_unix_milliseconds: int,
    ) -> ControlledStoreState:
        if self.root.exists() or self.root.is_symlink() or not self.root.parent.is_dir():
            raise ContractError("controlled store destination must be a new child")
        execution_id = canonical_content_id(
            {"nonce": nonce, "run_plan_id": plan.run_plan_id}, excluded=frozenset()
        )
        record = StoredExecutionRecord(
            schema_version="cernora.reference.stored-controlled-execution/v1",
            execution_id=execution_id,
            run_plan_id=plan.run_plan_id,
            nonce=nonce,
            started_unix_milliseconds=started_unix_milliseconds,
            wall_budget_milliseconds=plan.execution.max_total_wall_time_seconds * 1000,
            attempt_budget=plan.execution.max_attempt_count,
        )
        staging = Path(tempfile.mkdtemp(prefix=f".{self.root.name}.staging-", dir=self.root.parent))
        published = False
        try:
            (staging / "active").mkdir()
            (staging / "attempts").mkdir()
            (staging / "checkpoints").mkdir()
            (staging / "record.json").write_bytes(
                canonical_json_bytes(record.model_dump(mode="json"))
            )
            (staging / "run-plan.json").write_bytes(plan.canonical_bytes())
            atomic_publish_directory(staging, self.root)
            published = True
        finally:
            if not published:
                shutil.rmtree(staging, ignore_errors=True)
        return self.reload()

    def begin_attempt(
        self,
        *,
        trial_id: str,
        trial_slot_id: str,
        ordinal: int,
        predecessor_attempt_id: str | None,
        elapsed_before_attempt_milliseconds: int,
        started_unix_milliseconds: int,
    ) -> StoredActiveAttempt:
        state = self.reload()
        payload: dict[str, object] = {
            "schema_version": "cernora.reference.stored-active-attempt/v1",
            "execution_id": state.record.execution_id,
            "trial_id": trial_id,
            "trial_slot_id": trial_slot_id,
            "ordinal": ordinal,
            "predecessor_attempt_id": predecessor_attempt_id,
            "elapsed_before_attempt_milliseconds": elapsed_before_attempt_milliseconds,
            "started_unix_milliseconds": started_unix_milliseconds,
        }
        payload["active_id"] = canonical_content_id(payload, excluded=frozenset())
        active = StoredActiveAttempt.model_validate(payload)
        _write_once(
            self.root / "active" / trial_id / f"{ordinal:04d}.json",
            canonical_json_bytes(active.model_dump(mode="json")),
        )
        return active

    def publish_attempt(self, active: StoredActiveAttempt, attempt: ControlledAttempt) -> None:
        if (
            attempt.trial_id != active.trial_id
            or attempt.ordinal != active.ordinal
            or attempt.predecessor_attempt_id != active.predecessor_attempt_id
        ):
            raise ContractError("closed Attempt does not bind its immutable active record")
        destination = self.root / "attempts" / active.trial_id / f"{active.ordinal:04d}"
        if destination.exists() or destination.is_symlink():
            existing = _model(destination / "attempt.json", ControlledAttempt)
            if existing != attempt:
                raise ContractError("closed Attempt conflicts with existing immutable artifact")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".attempt.staging-", dir=destination.parent))
        published = False
        try:
            (staging / "attempt.json").write_bytes(
                canonical_json_bytes(attempt.model_dump(mode="json"))
            )
            atomic_publish_directory(staging, destination)
            published = True
        finally:
            if not published:
                shutil.rmtree(staging, ignore_errors=True)

    def checkpoint(
        self,
        *,
        status: Literal["running", "safe-stopped", "completed"],
        elapsed_milliseconds: int,
        completed_trial_ids: tuple[str, ...],
        observed_unix_milliseconds: int,
    ) -> StoredCheckpoint:
        state = self.reload()
        previous = state.checkpoints[-1] if state.checkpoints else None
        payload: dict[str, object] = {
            "schema_version": "cernora.reference.controlled-checkpoint/v1",
            "execution_id": state.record.execution_id,
            "sequence": 1 if previous is None else previous.sequence + 1,
            "previous_checkpoint_id": None if previous is None else previous.checkpoint_id,
            "status": status,
            "elapsed_milliseconds": elapsed_milliseconds,
            "attempt_count": len(state.attempts),
            "completed_trial_ids": completed_trial_ids,
            "observed_unix_milliseconds": observed_unix_milliseconds,
        }
        payload["checkpoint_id"] = canonical_content_id(payload, excluded=frozenset())
        checkpoint = StoredCheckpoint.model_validate(payload)
        _write_once(
            self.root / "checkpoints" / f"{checkpoint.sequence:08d}.json",
            canonical_json_bytes(checkpoint.model_dump(mode="json")),
        )
        return checkpoint

    def reload(self) -> ControlledStoreState:
        record = _model(self.root / "record.json", StoredExecutionRecord)
        assert isinstance(record, StoredExecutionRecord)
        plan = ControlledRunPlanV2.from_file(self.root / "run-plan.json")
        if plan.run_plan_id != record.run_plan_id:
            raise ContractError("stored RunPlan does not bind execution record")
        active_paths = sorted((self.root / "active").glob("*/*.json"))
        active_values = tuple(_model(path, StoredActiveAttempt) for path in active_paths)
        active = tuple(item for item in active_values if isinstance(item, StoredActiveAttempt))
        attempt_paths = sorted((self.root / "attempts").glob("*/*/attempt.json"))
        attempt_values = tuple(_model(path, ControlledAttempt) for path in attempt_paths)
        attempts = tuple(item for item in attempt_values if isinstance(item, ControlledAttempt))
        checkpoints_paths = sorted((self.root / "checkpoints").glob("*.json"))
        checkpoint_values = tuple(_model(path, StoredCheckpoint) for path in checkpoints_paths)
        checkpoints = tuple(
            item for item in checkpoint_values if isinstance(item, StoredCheckpoint)
        )
        if tuple(item.sequence for item in checkpoints) != tuple(range(1, len(checkpoints) + 1)):
            raise ContractError("stored checkpoint sequence is not contiguous")
        for previous, current in pairwise(checkpoints):
            if current.previous_checkpoint_id != previous.checkpoint_id or (
                current.elapsed_milliseconds < previous.elapsed_milliseconds
                or current.attempt_count < previous.attempt_count
            ):
                raise ContractError("stored checkpoint chain regressed")
        attempt_keys = {(item.trial_id, item.ordinal): item for item in attempts}
        if len(attempt_keys) != len(attempts):
            raise ContractError("stored Attempts are duplicated")
        active_keys = {(item.trial_id, item.ordinal): item for item in active}
        if len(active_keys) != len(active):
            raise ContractError("stored active Attempts are duplicated")
        missing = sorted(set(active_keys) - set(attempt_keys))
        if missing:
            raise ContractError("active Attempt has no verifiable closed artifact; resume blocked")
        for key, active_item in active_keys.items():
            attempt = attempt_keys[key]
            if (
                attempt.trial_id != active_item.trial_id
                or attempt.ordinal != active_item.ordinal
                or attempt.predecessor_attempt_id != active_item.predecessor_attempt_id
            ):
                raise ContractError("closed Attempt contradicts active record")
        checkpoint_attempts = checkpoints[-1].attempt_count if checkpoints else 0
        if checkpoint_attempts > len(attempts):
            raise ContractError("checkpoint references missing Attempt artifacts")
        adopted = tuple(item.attempt_id for item in attempts[checkpoint_attempts:])
        if len(attempts) > record.attempt_budget:
            raise ContractError("stored execution exceeds its frozen Attempt budget")
        return ControlledStoreState(
            record=record,
            plan=plan,
            active=active,
            attempts=attempts,
            checkpoints=checkpoints,
            adopted_attempt_ids=adopted,
        )

    def completed_result(self) -> ControlledExecutionResult:
        state = self.reload()
        if not state.checkpoints or state.checkpoints[-1].status != "completed":
            raise ContractError("stored execution is not complete")
        by_trial: dict[str, list[ControlledAttempt]] = {}
        for attempt in state.attempts:
            by_trial.setdefault(attempt.trial_id, []).append(attempt)
        trials = []
        for slot in state.plan.expand_trial_slots():
            trial_id = canonical_content_id(
                {
                    "execution_id": state.record.execution_id,
                    "trial_slot_id": slot.trial_slot_id,
                },
                excluded=frozenset(),
            )
            attempts = tuple(sorted(by_trial.get(trial_id, ()), key=lambda item: item.ordinal))
            if not attempts:
                raise ContractError("completed store omits one planned Trial")
            trials.append(
                ControlledTrialExecution(
                    schema_version="cernora.reference.controlled-trial-execution/v1",
                    trial_id=trial_id,
                    slot=slot,
                    attempts=attempts,
                    selected_attempt_id=attempts[-1].attempt_id,
                )
            )
        result = ControlledExecutionResult(
            schema_version="cernora.reference.controlled-execution-result/v1",
            execution_id=state.record.execution_id,
            run_plan_id=state.plan.run_plan_id,
            nonce=state.record.nonce,
            status="completed",
            budget_status="within_budget",
            trials=tuple(trials),
            attempt_count=len(state.attempts),
        )
        result.verify_plan(state.plan)
        return result


__all__ = [
    "ControlledExecutionStore",
    "ControlledStoreState",
    "StoredActiveAttempt",
    "StoredCheckpoint",
    "StoredExecutionRecord",
]
