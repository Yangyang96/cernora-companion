"""Durable claim-before-call execution for the development-only Agent pilot."""

from __future__ import annotations

import fcntl
import os
import secrets
import shutil
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, field_validator, model_validator

from cernora_reference_workflow.candidate_development import DevelopmentObservation
from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
    validate_relative_path,
)
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptExecutor,
    ControlledAttemptRequest,
    VerifiedControlledAttemptArtifact,
    publish_controlled_attempt_artifact,
    verify_controlled_attempt_artifact,
)
from cernora_reference_workflow.controlled_experiment_spec import Digest, StrictV2Contract
from cernora_reference_workflow.development_agent_pilot import (
    PILOT_MAX_ATTEMPTS,
    PILOT_MAX_WALL_SECONDS,
    PILOT_PREFLIGHT_FREE_BYTES,
    PILOT_SAFE_STOP_FREE_BYTES,
    DevelopmentAgentPilotPlan,
)
from cernora_reference_workflow.development_pilot_bundle import (
    _PLAN_TO_REQUEST_VERSION,
    DevelopmentPilotAuthorizationRequest,
)
from cernora_reference_workflow.publication import atomic_publish_directory

Clock = Callable[[], float]
WallClock = Callable[[], float]
Sleeper = Callable[[float], None]
DiskProbe = Callable[[Path], int]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
LedgerEvent = Literal[
    "prepared",
    "execution-started",
    "attempt-claimed",
    "attempt-published",
    "completed",
]


class AmbiguousDevelopmentPilotAttempt(ContractError):
    """A claimed external Attempt has no immutable terminal publication."""


class DevelopmentPilotStopped(ContractError):
    """A frozen development-pilot safety or budget bound stopped progress."""


class DevelopmentPilotExecutionRecord(StrictV2Contract):
    schema_version: Literal[
        "cernora.reference.development-pilot-execution/v1",
        "cernora.reference.development-pilot-execution/v2",
        "cernora.reference.development-pilot-execution/v3",
    ]
    execution_id: Digest
    plan_id: Digest
    nonce: Digest
    custody_path_sha256: Digest | None = None
    authorization_request_id: Digest | None = None
    prepared_unix_milliseconds: NonNegativeInt

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        if self.schema_version.endswith(("/v2", "/v3")):
            if self.custody_path_sha256 is None or self.authorization_request_id is None:
                raise ValueError("current execution must bind its request and custody path")
        elif self.custody_path_sha256 is not None or self.authorization_request_id is not None:
            raise ValueError("legacy execution cannot bind a request or custody path")
        identity: dict[str, object] = {"nonce": self.nonce, "plan_id": self.plan_id}
        if self.custody_path_sha256 is not None:
            identity["custody_path_sha256"] = self.custody_path_sha256
        if self.authorization_request_id is not None:
            identity["authorization_request_id"] = self.authorization_request_id
        expected = canonical_content_id(identity, excluded=frozenset())
        if self.execution_id != expected:
            raise ValueError("development pilot execution identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        if self.custody_path_sha256 is None:
            payload.pop("custody_path_sha256")
        if self.authorization_request_id is None:
            payload.pop("authorization_request_id")
        return canonical_json_bytes(payload)


class DevelopmentPilotLedgerEntry(StrictV2Contract):
    schema_version: Literal["cernora.reference.development-pilot-ledger-entry/v1"]
    entry_id: Digest
    execution_id: Digest
    plan_id: Digest
    sequence: PositiveInt
    previous_entry_sha256: Digest | None
    event: LedgerEvent
    observed_unix_milliseconds: NonNegativeInt
    elapsed_milliseconds: NonNegativeInt
    slot_index: PositiveInt | None
    trial_id: Digest | None
    ordinal: PositiveInt | None
    predecessor_attempt_id: Digest | None
    attempt_id: Digest | None
    attempt_artifact_id: Digest | None
    attempt_artifact_path: str | None
    outcome_id: Digest | None

    @model_validator(mode="after")
    def coherent_event_and_identity(self) -> Self:
        trial_fields = (self.slot_index, self.trial_id, self.ordinal)
        artifact_fields = (self.attempt_id, self.attempt_artifact_id, self.attempt_artifact_path)
        if self.event in {"attempt-claimed", "attempt-published"}:
            if any(item is None for item in trial_fields):
                raise ValueError("Attempt ledger event omits its exact Trial request")
        elif any(item is not None for item in (*trial_fields, self.predecessor_attempt_id)):
            raise ValueError("non-Attempt ledger event contains Trial request fields")
        if self.event == "attempt-published":
            if any(item is None for item in artifact_fields):
                raise ValueError("published Attempt event omits artifact authority")
            assert self.attempt_artifact_path is not None
            validate_relative_path(self.attempt_artifact_path)
        elif any(item is not None for item in artifact_fields):
            raise ValueError("non-publication event contains Attempt artifact authority")
        if (self.event == "completed") != (self.outcome_id is not None):
            raise ValueError("only completed event may bind the pilot outcome")
        if self.sequence == 1 and self.previous_entry_sha256 is not None:
            raise ValueError("first development pilot ledger entry cannot have a predecessor")
        if self.sequence > 1 and self.previous_entry_sha256 is None:
            raise ValueError("later development pilot ledger entry requires a predecessor")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"entry_id"})
        )
        if self.entry_id != expected:
            raise ValueError("development pilot ledger identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class DevelopmentPilotOutcome(StrictV2Contract):
    schema_version: Literal["cernora.reference.development-pilot-outcome/v1"]
    outcome_id: Digest
    execution_id: Digest
    plan_id: Digest
    status: Literal["candidate-eligible", "no-candidate", "inconclusive"]
    trial_count: Literal[6, 9]
    attempt_count: Annotated[StrictInt, Field(ge=6, le=18)]
    observations: tuple[DevelopmentObservation, ...]
    leading_failure_code: str | None

    @field_validator("observations", mode="before")
    @classmethod
    def tuple_observations(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def evidence_based_status(self) -> Self:
        ids = tuple(item.observation_id for item in self.observations)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("development pilot observations must be sorted and unique")
        failures = tuple(
            item for item in self.observations if item.agent_outcome == "behavioral-failure"
        )
        if self.status == "candidate-eligible":
            if not failures or self.leading_failure_code is None:
                raise ValueError("candidate eligibility requires an authoritative Agent failure")
        elif self.status == "no-candidate":
            if (
                len(self.observations) != self.trial_count
                or failures
                or self.leading_failure_code is not None
            ):
                raise ValueError(
                    "no-candidate requires an authoritative Agent pass for every Trial"
                )
        elif self.leading_failure_code is not None:
            raise ValueError("inconclusive pilot cannot select a failure mechanism")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"outcome_id"})
        )
        if self.outcome_id != expected:
            raise ValueError("development pilot outcome identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class DevelopmentPilotIncidentReceipt(StrictV2Contract):
    """Value-free durable classification for a claimed Attempt that did not publish."""

    schema_version: Literal["cernora.reference.development-pilot-incident/v1"]
    incident_id: Digest
    execution_id: Digest
    plan_id: Digest
    claim_entry_id: Digest
    phase: Literal["executor", "attempt-validation", "artifact-publication"]
    category: Literal[
        "controlled-attempt-error",
        "operator-interrupt",
        "unexpected-executor-error",
    ]
    observed_unix_milliseconds: NonNegativeInt

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"incident_id"})
        )
        if self.incident_id != expected:
            raise ValueError("development pilot incident identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class DevelopmentPilotStepResult(StrictV2Contract):
    execution_id: Digest
    plan_id: Digest
    status: Literal["prepared", "running", "completed"]
    completed_trial_count: Annotated[StrictInt, Field(ge=0, le=9)]
    attempt_count: Annotated[StrictInt, Field(ge=0, le=18)]
    outcome_id: Digest | None


@dataclass(frozen=True)
class DevelopmentPilotExecutionState:
    plan: DevelopmentAgentPilotPlan
    authorization_request: DevelopmentPilotAuthorizationRequest | None
    record: DevelopmentPilotExecutionRecord
    entries: tuple[DevelopmentPilotLedgerEntry, ...]
    attempts_by_slot: tuple[tuple[ControlledAttempt, ...], ...]
    artifacts: tuple[VerifiedControlledAttemptArtifact, ...]
    started_unix_milliseconds: int | None
    ambiguous_claim: DevelopmentPilotLedgerEntry | None
    adoptable_artifact: tuple[str, VerifiedControlledAttemptArtifact] | None
    incidents: tuple[DevelopmentPilotIncidentReceipt, ...]
    outcome: DevelopmentPilotOutcome | None

    @property
    def completed_trial_count(self) -> int:
        return sum(
            bool(attempts) and (not attempts[-1].retry_eligible or len(attempts) == 2)
            for attempts in self.attempts_by_slot
        )

    @property
    def attempt_count(self) -> int:
        return sum(map(len, self.attempts_by_slot))


def _disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _custody_path_sha256(path: Path, *, must_exist: bool) -> str:
    lexical = Path(os.path.abspath(path))
    if must_exist:
        resolved = path.resolve(strict=True)
    else:
        resolved = path.parent.resolve(strict=True) / path.name
    if path.is_symlink() or resolved != lexical:
        raise ContractError("development pilot custody path must have real non-symlink ancestry")
    return sha256_bytes(os.fsencode(resolved))


def _exclusive_file(path: Path, data: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        with suppress(OSError):
            path.unlink()
        raise
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _entry(
    *,
    record: DevelopmentPilotExecutionRecord,
    previous: DevelopmentPilotLedgerEntry | None,
    event: LedgerEvent,
    observed_unix_milliseconds: int,
    elapsed_milliseconds: int,
    slot_index: int | None = None,
    trial_id: str | None = None,
    ordinal: int | None = None,
    predecessor_attempt_id: str | None = None,
    attempt_id: str | None = None,
    attempt_artifact_id: str | None = None,
    attempt_artifact_path: str | None = None,
    outcome_id: str | None = None,
) -> DevelopmentPilotLedgerEntry:
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-ledger-entry/v1",
        "execution_id": record.execution_id,
        "plan_id": record.plan_id,
        "sequence": 1 if previous is None else previous.sequence + 1,
        "previous_entry_sha256": (
            None if previous is None else sha256_bytes(previous.canonical_bytes())
        ),
        "event": event,
        "observed_unix_milliseconds": observed_unix_milliseconds,
        "elapsed_milliseconds": elapsed_milliseconds,
        "slot_index": slot_index,
        "trial_id": trial_id,
        "ordinal": ordinal,
        "predecessor_attempt_id": predecessor_attempt_id,
        "attempt_id": attempt_id,
        "attempt_artifact_id": attempt_artifact_id,
        "attempt_artifact_path": attempt_artifact_path,
        "outcome_id": outcome_id,
    }
    payload["entry_id"] = canonical_content_id(payload, excluded=frozenset())
    return DevelopmentPilotLedgerEntry.model_validate(payload)


def _append_entry(root: Path, entry: DevelopmentPilotLedgerEntry) -> None:
    _exclusive_file(root / "ledger" / f"{entry.sequence:06d}.json", entry.canonical_bytes())


def _publish_incident(
    root: Path,
    *,
    record: DevelopmentPilotExecutionRecord,
    claim: DevelopmentPilotLedgerEntry,
    error: BaseException,
    phase: Literal["executor", "attempt-validation", "artifact-publication"],
    observed_unix_milliseconds: int,
) -> None:
    if isinstance(error, KeyboardInterrupt):
        category = "operator-interrupt"
    elif isinstance(error, ContractError):
        category = "controlled-attempt-error"
    else:
        category = "unexpected-executor-error"
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-incident/v1",
        "execution_id": record.execution_id,
        "plan_id": record.plan_id,
        "claim_entry_id": claim.entry_id,
        "phase": phase,
        "category": category,
        "observed_unix_milliseconds": observed_unix_milliseconds,
    }
    payload["incident_id"] = canonical_content_id(payload, excluded=frozenset())
    receipt = DevelopmentPilotIncidentReceipt.model_validate(payload)
    path = root / "diagnostics" / f"{claim.entry_id}.json"
    if path.exists():
        existing = _load_json_model(path, DevelopmentPilotIncidentReceipt)
        if existing != receipt:
            raise ContractError("development pilot incident receipt changed")
        return
    _exclusive_file(path, receipt.canonical_bytes())


@contextmanager
def _writer_lock(root: Path) -> Iterator[None]:
    descriptor = os.open(root / ".writer.lock", os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContractError("development pilot already has an active writer") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def prepare_development_pilot_execution(
    plan: DevelopmentAgentPilotPlan,
    destination: Path,
    *,
    authorization_request: DevelopmentPilotAuthorizationRequest,
    nonce: str | None = None,
    wall_clock: WallClock = time.time,
    disk_free: DiskProbe = _disk_free,
) -> DevelopmentPilotStepResult:
    """Prepare durable custody offline; this operation performs no external Attempt."""

    if plan.schema_version != "cernora.reference.development-agent-pilot-plan/v6":
        raise ContractError("development pilot prepare requires current Plan v6")
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ContractError("development pilot custody destination must be new")
    custody_path_sha256 = _custody_path_sha256(destination, must_exist=False)
    if (
        authorization_request.schema_version
        != "cernora.reference.development-pilot-authorization-request/v5"
        or authorization_request.plan_id != plan.plan_id
        or authorization_request.case_authority_sha256
        != tuple(item.authority_sha256 for item in plan.corpus.tasks)
        or authorization_request.attempt_envelope_timeout_seconds
        != plan.attempt_envelope_timeout_seconds
        or authorization_request.custody_path_sha256 != custody_path_sha256
    ):
        raise ContractError("development pilot request does not authorize this custody path")
    if disk_free(destination.parent) < PILOT_PREFLIGHT_FREE_BYTES:
        raise DevelopmentPilotStopped("disk_preflight_below_15_gib")
    selected_nonce = nonce or secrets.token_hex(32)
    if len(selected_nonce) != 64 or any(c not in "0123456789abcdef" for c in selected_nonce):
        raise ContractError("development pilot nonce must be 32-byte lowercase hex")
    prepared_ms = int(wall_clock() * 1000)
    record = DevelopmentPilotExecutionRecord(
        schema_version="cernora.reference.development-pilot-execution/v3",
        execution_id=canonical_content_id(
            {
                "authorization_request_id": authorization_request.request_id,
                "custody_path_sha256": custody_path_sha256,
                "nonce": selected_nonce,
                "plan_id": plan.plan_id,
            },
            excluded=frozenset(),
        ),
        plan_id=plan.plan_id,
        nonce=selected_nonce,
        custody_path_sha256=custody_path_sha256,
        authorization_request_id=authorization_request.request_id,
        prepared_unix_milliseconds=prepared_ms,
    )
    staging = Path(
        os.path.realpath(
            Path(destination.parent) / f".{destination.name}.staging-{secrets.token_hex(12)}"
        )
    )
    staging.mkdir(mode=0o700)
    published = False
    try:
        (staging / "artifacts").mkdir()
        (staging / "diagnostics").mkdir()
        (staging / "ledger").mkdir()
        (staging / ".writer.lock").write_bytes(b"")
        (staging / "authorization-request.json").write_bytes(
            authorization_request.canonical_bytes()
        )
        (staging / "plan.json").write_bytes(plan.canonical_bytes())
        (staging / "record.json").write_bytes(record.canonical_bytes())
        prepared = _entry(
            record=record,
            previous=None,
            event="prepared",
            observed_unix_milliseconds=prepared_ms,
            elapsed_milliseconds=0,
        )
        (staging / "ledger" / "000001.json").write_bytes(prepared.canonical_bytes())
        atomic_publish_directory(staging, destination)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)
    return DevelopmentPilotStepResult(
        execution_id=record.execution_id,
        plan_id=plan.plan_id,
        status="prepared",
        completed_trial_count=0,
        attempt_count=0,
        outcome_id=None,
    )


def _load_json_model(path: Path, model: type[StrictV2Contract]) -> StrictV2Contract:
    raw = read_regular_file_bytes(path)
    payload = load_json_bytes(raw)
    if not isinstance(payload, dict):
        raise ContractError("development pilot custody JSON must be one object")
    value = model.model_validate(payload)
    canonical = getattr(value, "canonical_bytes", None)
    expected = (
        canonical() if callable(canonical) else canonical_json_bytes(value.model_dump(mode="json"))
    )
    if raw != expected:
        raise ContractError("development pilot custody JSON is not canonical")
    return value


def _load_authorization_request(path: Path) -> DevelopmentPilotAuthorizationRequest:
    raw = read_regular_file_bytes(path)
    request = DevelopmentPilotAuthorizationRequest.model_validate(load_json_bytes(raw))
    if raw != request.canonical_bytes():
        raise ContractError("development pilot authorization request is not canonical")
    return request


def _derive_outcome(
    plan: DevelopmentAgentPilotPlan,
    record: DevelopmentPilotExecutionRecord,
    attempts_by_slot: tuple[tuple[ControlledAttempt, ...], ...],
) -> DevelopmentPilotOutcome:
    observations: list[DevelopmentObservation] = []
    failures: Counter[str] = Counter()
    incomplete = False
    for task, attempts in zip(plan.corpus.tasks, attempts_by_slot, strict=True):
        if not attempts or (attempts[-1].retry_eligible and len(attempts) == 1):
            raise ContractError("development pilot outcome was requested before all Trials closed")
        selected = attempts[-1]
        result = selected.repair_result
        if result is None:
            incomplete = True
            continue
        if result.passed:
            failure_code = None
            agent_outcome = "pass"
        else:
            # A real Agent may fail the declared check and additionally violate
            # protected-path authority in the same attempt; the declared code
            # must be present, and extra codes stay part of the frozen result.
            if task.failure_code not in result.failure_codes:
                raise ContractError("development Agent failure code contradicts task authority")
            failure_code = task.failure_code
            agent_outcome = "behavioral-failure"
            failures[failure_code] += 1
        observations.append(
            DevelopmentObservation(
                observation_id=f"agent-{task.case.case_id}-{selected.attempt_id[:16]}",
                case_id=task.case.case_id,
                split=task.split_id,  # type: ignore[arg-type]
                source="agent-pilot",
                agent_outcome=agent_outcome,  # type: ignore[arg-type]
                failure_code=failure_code,
                evidence_sha256=sha256_bytes(
                    canonical_json_bytes(selected.model_dump(mode="json"))
                ),
            )
        )
    ordered = tuple(sorted(observations, key=lambda item: item.observation_id))
    leading = None
    if incomplete:
        status = "inconclusive"
    elif failures:
        status = "candidate-eligible"
        leading = sorted(failures.items(), key=lambda item: (-item[1], item[0]))[0][0]
    else:
        status = "no-candidate"
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-outcome/v1",
        "execution_id": record.execution_id,
        "plan_id": plan.plan_id,
        "status": status,
        "trial_count": plan.planned_trial_count,
        "attempt_count": sum(map(len, attempts_by_slot)),
        "observations": [item.model_dump(mode="json") for item in ordered],
        "leading_failure_code": leading,
    }
    payload["outcome_id"] = canonical_content_id(payload, excluded=frozenset())
    return DevelopmentPilotOutcome.model_validate(payload)


def inspect_development_pilot_execution(root: Path) -> DevelopmentPilotExecutionState:
    """Strictly replay custody, artifacts, and the claim/publication ledger offline."""

    entries = {item.name: item for item in root.iterdir()}
    allowed = {
        ".writer.lock",
        "artifacts",
        "diagnostics",
        "ledger",
        "authorization-request.json",
        "plan.json",
        "record.json",
        "outcome.json",
    }
    if (
        not {".writer.lock", "artifacts", "ledger", "plan.json", "record.json"}.issubset(entries)
        or set(entries) - allowed
    ):
        raise ContractError("development pilot custody root is open or incomplete")
    if (
        not entries["artifacts"].is_dir()
        or entries["artifacts"].is_symlink()
        or not entries["ledger"].is_dir()
        or entries["ledger"].is_symlink()
    ):
        raise ContractError("development pilot custody directories are ambiguous")
    plan = DevelopmentAgentPilotPlan.from_file(entries["plan.json"])
    record_value = _load_json_model(entries["record.json"], DevelopmentPilotExecutionRecord)
    assert isinstance(record_value, DevelopmentPilotExecutionRecord)
    record = record_value
    current = plan.schema_version.endswith("/v6")
    request_bound = record.schema_version.endswith(("/v2", "/v3"))
    if (current or request_bound) and (
        {"diagnostics", "authorization-request.json"} - set(entries)
    ):
        raise ContractError("bound development pilot custody omits bound authorities")
    if not (current or request_bound) and "diagnostics" in entries:
        raise ContractError("legacy development pilot custody cannot contain diagnostics")
    if not (current or request_bound) and "authorization-request.json" in entries:
        raise ContractError("legacy development pilot custody cannot contain a current request")
    if "diagnostics" in entries and (
        not entries["diagnostics"].is_dir() or entries["diagnostics"].is_symlink()
    ):
        raise ContractError("development pilot diagnostics directory is ambiguous")
    if record.plan_id != plan.plan_id:
        raise ContractError("development pilot record binds another Plan")
    actual_custody_path_sha256 = _custody_path_sha256(root, must_exist=True)
    if record.custody_path_sha256 is not None and (
        record.custody_path_sha256 != actual_custody_path_sha256
    ):
        raise ContractError("development pilot record binds another custody path")
    authorization_request = (
        _load_authorization_request(entries["authorization-request.json"])
        if "authorization-request.json" in entries
        else None
    )
    if authorization_request is not None and (
        authorization_request.schema_version != _PLAN_TO_REQUEST_VERSION[plan.schema_version]
        or authorization_request.request_id != record.authorization_request_id
        or authorization_request.plan_id != plan.plan_id
        or authorization_request.case_authority_sha256
        != tuple(item.authority_sha256 for item in plan.corpus.tasks)
        or authorization_request.attempt_envelope_timeout_seconds
        != plan.attempt_envelope_timeout_seconds
        or authorization_request.custody_path_sha256 != actual_custody_path_sha256
    ):
        raise ContractError("development pilot authorization request contradicts custody")
    ledger_files = closed_regular_tree(entries["ledger"])
    expected_names = tuple(f"{index:06d}.json" for index in range(1, len(ledger_files) + 1))
    if tuple(ledger_files) != expected_names or not ledger_files:
        raise ContractError("development pilot ledger sequence is not contiguous")
    ledger: list[DevelopmentPilotLedgerEntry] = []
    for name in expected_names:
        value = _load_json_model(ledger_files[name], DevelopmentPilotLedgerEntry)
        assert isinstance(value, DevelopmentPilotLedgerEntry)
        previous = ledger[-1] if ledger else None
        expected_previous = None if previous is None else sha256_bytes(previous.canonical_bytes())
        if (
            value.sequence != len(ledger) + 1
            or value.previous_entry_sha256 != expected_previous
            or value.plan_id != plan.plan_id
            or value.execution_id != record.execution_id
            or (
                previous is not None
                and value.observed_unix_milliseconds < previous.observed_unix_milliseconds
            )
        ):
            raise ContractError("development pilot ledger chain is invalid")
        ledger.append(value)
    if ledger[0].event != "prepared" or any(item.event == "prepared" for item in ledger[1:]):
        raise ContractError("development pilot prepared event is not unique and first")

    slots = plan.expand_trial_slots()
    specs = {item.task.task_id: item for item in plan.experiment_specs}
    attempts_by_slot: list[list[ControlledAttempt]] = [[] for _ in slots]
    artifacts: list[VerifiedControlledAttemptArtifact] = []
    expected_artifact_paths: set[str] = set()
    started_ms: int | None = None
    pending_claim: DevelopmentPilotLedgerEntry | None = None
    completed = False
    for entry in ledger[1:]:
        if completed:
            raise ContractError("development pilot ledger continues after completion")
        if entry.event == "execution-started":
            if started_ms is not None or pending_claim is not None or artifacts:
                raise ContractError("development pilot execution-started event is misplaced")
            started_ms = entry.observed_unix_milliseconds
        elif entry.event == "attempt-claimed":
            if started_ms is None or pending_claim is not None:
                raise ContractError("development pilot Attempt claim is misplaced")
            assert entry.slot_index is not None
            if entry.slot_index > len(slots):
                raise ContractError("development pilot Attempt claim selects an unknown slot")
            slot_attempts = attempts_by_slot[entry.slot_index - 1]
            slot = slots[entry.slot_index - 1]
            expected_trial = canonical_content_id(
                {"execution_id": record.execution_id, "trial_slot_id": slot.trial_slot_id},
                excluded=frozenset(),
            )
            expected_predecessor = slot_attempts[-1].attempt_id if slot_attempts else None
            if (
                entry.trial_id != expected_trial
                or entry.ordinal != len(slot_attempts) + 1
                or entry.predecessor_attempt_id != expected_predecessor
                or entry.slot_index
                != next(
                    (
                        index
                        for index, attempts in enumerate(attempts_by_slot, start=1)
                        if not attempts or (attempts[-1].retry_eligible and len(attempts) == 1)
                    ),
                    None,
                )
            ):
                raise ContractError("development pilot Attempt claim is out of order")
            pending_claim = entry
        elif entry.event == "attempt-published":
            if pending_claim is None:
                raise ContractError("development pilot publication has no prior claim")
            assert entry.slot_index is not None
            if (
                entry.slot_index != pending_claim.slot_index
                or entry.trial_id != pending_claim.trial_id
                or entry.ordinal != pending_claim.ordinal
                or entry.predecessor_attempt_id != pending_claim.predecessor_attempt_id
            ):
                raise ContractError("development pilot publication does not close its claim")
            assert entry.attempt_artifact_path is not None
            artifact_root = root / entry.attempt_artifact_path
            artifact = verify_controlled_attempt_artifact(artifact_root)
            slot = slots[entry.slot_index - 1]
            spec = specs[slot.case_id]
            attempt = artifact.attempt
            if (
                artifact.manifest.artifact_id != entry.attempt_artifact_id
                or attempt.attempt_id != entry.attempt_id
                or attempt.trial_id != entry.trial_id
                or attempt.ordinal != entry.ordinal
                or attempt.predecessor_attempt_id != entry.predecessor_attempt_id
            ):
                raise ContractError("development pilot Attempt artifact contradicts ledger")
            attempt.verify_authority(spec)
            attempts_by_slot[entry.slot_index - 1].append(attempt)
            if len(attempts_by_slot[entry.slot_index - 1]) == 2 and attempt.retry_eligible:
                raise ContractError("development pilot exhausted retry remains retry eligible")
            expected_artifact_paths.add(entry.attempt_artifact_path)
            artifacts.append(artifact)
            pending_claim = None
        elif entry.event == "completed":
            if pending_claim is not None or any(
                not attempts or (attempts[-1].retry_eligible and len(attempts) == 1)
                for attempts in attempts_by_slot
            ):
                raise ContractError("development pilot completed before every Trial closed")
            completed = True
        else:
            raise ContractError("development pilot ledger event is out of order")
    actual_artifact_paths: set[str] = set()
    for path in (root / "artifacts").iterdir():
        if not path.is_dir() or path.is_symlink():
            raise ContractError("development pilot Attempt artifacts contain an unknown entry")
        manifest = path / "manifest.json"
        if not manifest.is_file() or manifest.is_symlink():
            raise ContractError("development pilot Attempt artifact omits a real manifest")
        actual_artifact_paths.add(path.relative_to(root).as_posix())
    missing_artifacts = expected_artifact_paths - actual_artifact_paths
    extra_artifacts = actual_artifact_paths - expected_artifact_paths
    if missing_artifacts:
        raise ContractError("development pilot Attempt artifacts are orphaned or missing")
    adoptable_artifact: tuple[str, VerifiedControlledAttemptArtifact] | None = None
    if extra_artifacts:
        expected_orphan = None
        if pending_claim is not None:
            assert pending_claim.slot_index is not None and pending_claim.ordinal is not None
            expected_orphan = (
                f"artifacts/{pending_claim.slot_index:04d}-{pending_claim.ordinal:02d}"
            )
        if extra_artifacts != ({expected_orphan} if expected_orphan is not None else set()):
            raise ContractError("development pilot Attempt artifacts are orphaned or missing")
        assert expected_orphan is not None and pending_claim is not None
        orphan = verify_controlled_attempt_artifact(root / expected_orphan)
        orphan_attempt = orphan.attempt
        assert pending_claim.slot_index is not None
        orphan_attempt.verify_authority(plan.experiment_specs[pending_claim.slot_index - 1])
        if (
            orphan_attempt.trial_id != pending_claim.trial_id
            or orphan_attempt.ordinal != pending_claim.ordinal
            or orphan_attempt.predecessor_attempt_id != pending_claim.predecessor_attempt_id
        ):
            raise ContractError("development pilot orphan artifact contradicts active claim")
        adoptable_artifact = (expected_orphan, orphan)
    attempts_tuple = tuple(tuple(items) for items in attempts_by_slot)
    expected_outcome = None
    outcome_path = entries.get("outcome.json")
    all_closed = all(
        attempts and (not attempts[-1].retry_eligible or len(attempts) == 2)
        for attempts in attempts_tuple
    )
    if outcome_path is not None:
        value = _load_json_model(outcome_path, DevelopmentPilotOutcome)
        assert isinstance(value, DevelopmentPilotOutcome)
        expected_outcome = _derive_outcome(plan, record, attempts_tuple)
        if value != expected_outcome:
            raise ContractError("development pilot outcome is not derived from exact Attempts")
    if completed:
        if expected_outcome is None or ledger[-1].outcome_id != expected_outcome.outcome_id:
            raise ContractError("development pilot completion omits its exact outcome")
    elif outcome_path is not None and not all_closed:
        raise ContractError("development pilot outcome appeared before all Trials closed")
    incidents: list[DevelopmentPilotIncidentReceipt] = []
    if "diagnostics" in entries:
        claim_ids = {item.entry_id for item in ledger if item.event == "attempt-claimed"}
        for name, path in closed_regular_tree(entries["diagnostics"]).items():
            value = _load_json_model(path, DevelopmentPilotIncidentReceipt)
            assert isinstance(value, DevelopmentPilotIncidentReceipt)
            if (
                name != f"{value.claim_entry_id}.json"
                or value.claim_entry_id not in claim_ids
                or value.execution_id != record.execution_id
                or value.plan_id != plan.plan_id
                or pending_claim is None
                or value.claim_entry_id != pending_claim.entry_id
                or value.observed_unix_milliseconds < pending_claim.observed_unix_milliseconds
            ):
                raise ContractError("development pilot incident contradicts custody")
            incidents.append(value)
    if incidents and adoptable_artifact is not None:
        raise ContractError("development pilot incident contradicts an adoptable artifact")
    return DevelopmentPilotExecutionState(
        plan=plan,
        authorization_request=authorization_request,
        record=record,
        entries=tuple(ledger),
        attempts_by_slot=attempts_tuple,
        artifacts=tuple(artifacts),
        started_unix_milliseconds=started_ms,
        ambiguous_claim=pending_claim,
        adoptable_artifact=adoptable_artifact,
        incidents=tuple(incidents),
        outcome=expected_outcome if completed else None,
    )


def summarize_development_pilot_execution(root: Path) -> DevelopmentPilotStepResult:
    """Strictly replay custody and report whether external execution has started."""

    state = inspect_development_pilot_execution(root)
    if state.outcome is not None:
        status: Literal["prepared", "running", "completed"] = "completed"
    elif any(entry.event == "execution-started" for entry in state.entries):
        status = "running"
    else:
        status = "prepared"
    return DevelopmentPilotStepResult(
        execution_id=state.record.execution_id,
        plan_id=state.plan.plan_id,
        status=status,
        completed_trial_count=state.completed_trial_count,
        attempt_count=state.attempt_count,
        outcome_id=None if state.outcome is None else state.outcome.outcome_id,
    )


def _finish_if_complete(
    root: Path,
    state: DevelopmentPilotExecutionState,
    *,
    observed_ms: int,
) -> DevelopmentPilotOutcome | None:
    if state.completed_trial_count != state.plan.planned_trial_count:
        return None
    outcome = _derive_outcome(state.plan, state.record, state.attempts_by_slot)
    outcome_path = root / "outcome.json"
    if outcome_path.exists():
        existing = _load_json_model(outcome_path, DevelopmentPilotOutcome)
        if existing != outcome:
            raise ContractError("development pilot prepublished outcome changed")
    else:
        _exclusive_file(outcome_path, outcome.canonical_bytes())
    assert state.started_unix_milliseconds is not None
    completed = _entry(
        record=state.record,
        previous=state.entries[-1],
        event="completed",
        observed_unix_milliseconds=observed_ms,
        elapsed_milliseconds=max(0, observed_ms - state.started_unix_milliseconds),
        outcome_id=outcome.outcome_id,
    )
    _append_entry(root, completed)
    return outcome


def step_development_pilot_execution(
    root: Path,
    executor: ControlledAttemptExecutor,
    *,
    accepted_plan_id: str,
    accepted_request_id: str,
    wall_clock: WallClock = time.time,
    clock: Clock = time.monotonic,
    sleeper: Sleeper = time.sleep,
    disk_free: DiskProbe = _disk_free,
) -> DevelopmentPilotStepResult:
    """Claim and execute at most one external Attempt under the exact accepted Plan ID."""

    with _writer_lock(root):
        state = inspect_development_pilot_execution(root)
        if state.plan.schema_version != "cernora.reference.development-agent-pilot-plan/v6":
            raise ContractError("development pilot step requires current Plan v6")
        if accepted_plan_id != state.plan.plan_id:
            raise ContractError("development pilot acceptance does not equal the exact Plan ID")
        if accepted_request_id != state.record.authorization_request_id:
            raise ContractError("development pilot acceptance does not equal the exact request ID")
        if state.adoptable_artifact is not None:
            claim = state.ambiguous_claim
            assert claim is not None and state.started_unix_milliseconds is not None
            artifact_path, artifact = state.adoptable_artifact
            adopted_ms = max(claim.observed_unix_milliseconds, int(wall_clock() * 1000))
            adopted = _entry(
                record=state.record,
                previous=state.entries[-1],
                event="attempt-published",
                observed_unix_milliseconds=adopted_ms,
                elapsed_milliseconds=adopted_ms - state.started_unix_milliseconds,
                slot_index=claim.slot_index,
                trial_id=claim.trial_id,
                ordinal=claim.ordinal,
                predecessor_attempt_id=claim.predecessor_attempt_id,
                attempt_id=artifact.attempt.attempt_id,
                attempt_artifact_id=artifact.manifest.artifact_id,
                attempt_artifact_path=artifact_path,
            )
            _append_entry(root, adopted)
            state = inspect_development_pilot_execution(root)
            outcome = _finish_if_complete(root, state, observed_ms=adopted_ms)
            return DevelopmentPilotStepResult(
                execution_id=state.record.execution_id,
                plan_id=state.plan.plan_id,
                status="completed" if outcome is not None else "running",
                completed_trial_count=state.completed_trial_count,
                attempt_count=state.attempt_count,
                outcome_id=None if outcome is None else outcome.outcome_id,
            )
        elif state.ambiguous_claim is not None:
            raise AmbiguousDevelopmentPilotAttempt(
                "development pilot has an active claim without a terminal publication"
            )
        if state.outcome is not None:
            return DevelopmentPilotStepResult(
                execution_id=state.record.execution_id,
                plan_id=state.plan.plan_id,
                status="completed",
                completed_trial_count=state.plan.planned_trial_count,
                attempt_count=state.attempt_count,
                outcome_id=state.outcome.outcome_id,
            )
        now_ms = int(wall_clock() * 1000)
        if state.started_unix_milliseconds is None:
            started = _entry(
                record=state.record,
                previous=state.entries[-1],
                event="execution-started",
                observed_unix_milliseconds=now_ms,
                elapsed_milliseconds=0,
            )
            _append_entry(root, started)
            state = inspect_development_pilot_execution(root)
        assert state.started_unix_milliseconds is not None
        elapsed_ms = now_ms - state.started_unix_milliseconds
        if elapsed_ms < 0:
            raise DevelopmentPilotStopped("wall_clock_moved_backward")
        now_ms = max(now_ms, state.entries[-1].observed_unix_milliseconds)
        elapsed_ms = now_ms - state.started_unix_milliseconds
        if elapsed_ms >= PILOT_MAX_WALL_SECONDS * 1000:
            raise DevelopmentPilotStopped("hard_wall_deadline_elapsed")
        if disk_free(root) < PILOT_SAFE_STOP_FREE_BYTES:
            raise DevelopmentPilotStopped("disk_safe_stop_below_8_gib")
        outcome = _finish_if_complete(root, state, observed_ms=now_ms)
        if outcome is not None:
            return DevelopmentPilotStepResult(
                execution_id=state.record.execution_id,
                plan_id=state.plan.plan_id,
                status="completed",
                completed_trial_count=state.plan.planned_trial_count,
                attempt_count=state.attempt_count,
                outcome_id=outcome.outcome_id,
            )
        if state.attempt_count >= PILOT_MAX_ATTEMPTS:
            raise DevelopmentPilotStopped("attempt_budget_exhausted")
        if not executor.enforces_hard_deadline:
            raise ContractError("development pilot executor must enforce the active deadline")
        slot_index = next(
            index
            for index, attempts in enumerate(state.attempts_by_slot, start=1)
            if not attempts or (attempts[-1].retry_eligible and len(attempts) == 1)
        )
        attempts = state.attempts_by_slot[slot_index - 1]
        slot = state.plan.expand_trial_slots()[slot_index - 1]
        spec = state.plan.experiment_specs[slot_index - 1]
        if attempts:
            prior_publication = next(
                entry
                for entry in reversed(state.entries)
                if entry.event == "attempt-published" and entry.slot_index == slot_index
            )
            delay_ms = spec.retry.delay_seconds * 1000
            remaining_delay = prior_publication.observed_unix_milliseconds + delay_ms - now_ms
            if remaining_delay > 0:
                sleeper(remaining_delay / 1000)
                now_ms = int(wall_clock() * 1000)
                if now_ms < state.started_unix_milliseconds:
                    raise DevelopmentPilotStopped("wall_clock_moved_backward")
                now_ms = max(now_ms, state.entries[-1].observed_unix_milliseconds)
                if now_ms - state.started_unix_milliseconds >= PILOT_MAX_WALL_SECONDS * 1000:
                    raise DevelopmentPilotStopped("retry_delay_reached_hard_wall_deadline")
        trial_id = canonical_content_id(
            {"execution_id": state.record.execution_id, "trial_slot_id": slot.trial_slot_id},
            excluded=frozenset(),
        )
        ordinal = len(attempts) + 1
        predecessor = attempts[-1].attempt_id if attempts else None
        claim = _entry(
            record=state.record,
            previous=state.entries[-1],
            event="attempt-claimed",
            observed_unix_milliseconds=now_ms,
            elapsed_milliseconds=now_ms - state.started_unix_milliseconds,
            slot_index=slot_index,
            trial_id=trial_id,
            ordinal=ordinal,
            predecessor_attempt_id=predecessor,
        )
        _append_entry(root, claim)
        deadline = clock() + max(
            0.0,
            PILOT_MAX_WALL_SECONDS - (now_ms - state.started_unix_milliseconds) / 1000,
        )
        request = ControlledAttemptRequest(
            trial_id=trial_id,
            slot=slot,
            specification=spec,
            ordinal=ordinal,
            predecessor_attempt_id=predecessor,
            global_deadline_monotonic=deadline,
        )
        try:
            attempt = executor(request)
        except BaseException as error:
            _publish_incident(
                root,
                record=state.record,
                claim=claim,
                error=error,
                phase="executor",
                observed_unix_milliseconds=max(
                    claim.observed_unix_milliseconds,
                    int(wall_clock() * 1000),
                ),
            )
            raise
        try:
            if clock() > deadline:
                raise DevelopmentPilotStopped("attempt_returned_after_hard_deadline")
            if (
                attempt.trial_id != trial_id
                or attempt.ordinal != ordinal
                or attempt.predecessor_attempt_id != predecessor
            ):
                raise ContractError("development pilot executor returned another Attempt request")
            attempt.verify_authority(spec)
        except BaseException as error:
            _publish_incident(
                root,
                record=state.record,
                claim=claim,
                error=error,
                phase="attempt-validation",
                observed_unix_milliseconds=max(
                    claim.observed_unix_milliseconds,
                    int(wall_clock() * 1000),
                ),
            )
            raise
        artifact_path = f"artifacts/{slot_index:04d}-{ordinal:02d}"
        try:
            artifact = publish_controlled_attempt_artifact(
                root / artifact_path,
                attempt=attempt,
                specification=spec,
            )
        except BaseException as error:
            if not (root / artifact_path).exists():
                _publish_incident(
                    root,
                    record=state.record,
                    claim=claim,
                    error=error,
                    phase="artifact-publication",
                    observed_unix_milliseconds=max(
                        claim.observed_unix_milliseconds,
                        int(wall_clock() * 1000),
                    ),
                )
            raise
        published_ms = max(claim.observed_unix_milliseconds, int(wall_clock() * 1000))
        published = _entry(
            record=state.record,
            previous=claim,
            event="attempt-published",
            observed_unix_milliseconds=published_ms,
            elapsed_milliseconds=published_ms - state.started_unix_milliseconds,
            slot_index=slot_index,
            trial_id=trial_id,
            ordinal=ordinal,
            predecessor_attempt_id=predecessor,
            attempt_id=attempt.attempt_id,
            attempt_artifact_id=artifact.manifest.artifact_id,
            attempt_artifact_path=artifact_path,
        )
        _append_entry(root, published)
        updated = inspect_development_pilot_execution(root)
        outcome = _finish_if_complete(root, updated, observed_ms=published_ms)
        if outcome is not None:
            return DevelopmentPilotStepResult(
                execution_id=updated.record.execution_id,
                plan_id=updated.plan.plan_id,
                status="completed",
                completed_trial_count=state.plan.planned_trial_count,
                attempt_count=updated.attempt_count,
                outcome_id=outcome.outcome_id,
            )
        return DevelopmentPilotStepResult(
            execution_id=updated.record.execution_id,
            plan_id=updated.plan.plan_id,
            status="running",
            completed_trial_count=updated.completed_trial_count,
            attempt_count=updated.attempt_count,
            outcome_id=None,
        )


__all__ = [
    "AmbiguousDevelopmentPilotAttempt",
    "DevelopmentPilotExecutionRecord",
    "DevelopmentPilotExecutionState",
    "DevelopmentPilotIncidentReceipt",
    "DevelopmentPilotLedgerEntry",
    "DevelopmentPilotOutcome",
    "DevelopmentPilotStepResult",
    "DevelopmentPilotStopped",
    "inspect_development_pilot_execution",
    "prepare_development_pilot_execution",
    "step_development_pilot_execution",
    "summarize_development_pilot_execution",
]
