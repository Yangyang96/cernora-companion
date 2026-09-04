from __future__ import annotations

import importlib.metadata
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

import cernora_reference_workflow.runtime_diagnostic_pilot as diagnostic_module
from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
)
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptRequest,
)
from cernora_reference_workflow.controlled_live_attempt import ControlledHarborAttemptExecutor
from cernora_reference_workflow.development_agent_pilot import (
    PILOT_CASE_IDS,
    DevelopmentAgentPilotPlan,
    build_development_agent_pilot_plan,
    load_development_pilot_corpus,
    materialize_development_pilot_image_set,
)
from cernora_reference_workflow.runtime_diagnostic_pilot import (
    AmbiguousRuntimeDiagnosticAttempt,
    RuntimeDiagnosticPilotPlan,
    build_runtime_diagnostic_authorization_request,
    build_runtime_diagnostic_pilot_plan,
    create_runtime_diagnostic_proposal,
    inspect_runtime_diagnostic_pilot,
    inspect_runtime_diagnostic_proposal,
    prepare_runtime_diagnostic_pilot,
    verify_runtime_diagnostic_runtime,
)
from cernora_reference_workflow.runtime_diagnostic_pilot import (
    _step_runtime_diagnostic_pilot as step_runtime_diagnostic_pilot,
)
from cernora_reference_workflow.study_preparation import ImplementationCandidate
from tests.unit.test_controlled_execution import lifecycle_attempt
from tests.unit.test_controlled_live_attempt import (
    AgentTimeoutResultProcess,
    FakeContainers,
    _auth_file,
    _proxy_environment,
)
from tests.unit.test_study_preparation import _candidate_wheels

FREE = 20 * 1024**3
ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "examples" / "priority4-development-pilot"
SOURCE_PI_PLAN_ID = "fc6bfeff2dde3a221513ac11bec9f0b94e43f8c826f04213cc1a362490f6e8f9"


def _source_plan() -> DevelopmentAgentPilotPlan:
    """Build the deterministically reproducible pi-era source development pilot Plan.

    The historical ``preparations/next-priority4-development-pilot-repair`` bundle is
    Codex-era evidence frozen by the era boundary; its identity cannot be regenerated
    offline because it binds the original published wheels and Docker-built image
    digests. The diagnostic authority instead pins this reproducible pi-era Plan built
    from the same fresh corpus with an exact baseline shape.
    """

    corpus = load_development_pilot_corpus(CORPUS)
    images = materialize_development_pilot_image_set(
        build_base_image="cernora-reference/pi-runtime@sha256:" + "a" * 64,
        images={
            case_id: f"cernora-reference/p4-pilot-{case_id}@sha256:{index:064x}"
            for index, case_id in enumerate(PILOT_CASE_IDS, start=1)
        },
    )
    plan = build_development_agent_pilot_plan(
        corpus=corpus,
        images=images,
        implementation_candidates=(
            ImplementationCandidate(
                name="cernora", version="0.1.4", kind="wheel", size=1, sha256="b" * 64
            ),
            ImplementationCandidate(
                name="cernora-reference-workflow",
                version="0.4.0",
                kind="wheel",
                size=1,
                sha256="c" * 64,
            ),
        ),
    )
    assert plan.plan_id == SOURCE_PI_PLAN_ID
    return plan


def _plan() -> RuntimeDiagnosticPilotPlan:
    source = _source_plan()
    candidates = source.implementation_candidates
    assert candidates is not None
    return build_runtime_diagnostic_pilot_plan(
        source,
        implementation_candidates=candidates,
    )


def _prepare(tmp_path: Path) -> tuple[RuntimeDiagnosticPilotPlan, Path]:
    plan = _plan()
    custody = tmp_path / f"runtime-diagnostic-{plan.plan_id}"
    request = build_runtime_diagnostic_authorization_request(plan, custody=custody)
    state = prepare_runtime_diagnostic_pilot(
        plan,
        request,
        custody,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=request.request_id,
        nonce="f" * 64,
        wall_clock=lambda: 1000.0,
        disk_free=lambda _: FREE,
    )
    assert state.status == "prepared"
    return plan, custody


def test_exact_closed_agent_timeout_publishes_usable_terminal_evidence(tmp_path: Path) -> None:
    plan, custody = _prepare(tmp_path)
    evaluation = tmp_path / "evaluation"
    evaluation.mkdir()
    containers = FakeContainers()
    executor = ControlledHarborAttemptExecutor(
        repository_root=tmp_path,
        tasks=(plan.task,),
        evaluation_root=evaluation,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=AgentTimeoutResultProcess(plan.task, plan.specification),
        container_controller=containers,
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
        close_unusable_runtime_evidence=True,
        attempt_envelope_grace_seconds=60,
    )
    prepared = inspect_runtime_diagnostic_pilot(custody)

    state = step_runtime_diagnostic_pilot(
        custody,
        executor,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=prepared.request.request_id,
        wall_clock=lambda: 1001.0,
        disk_free=lambda _: FREE,
    )

    assert state.status == "completed"
    assert state.outcome is not None
    assert state.outcome.classification == "timed-out"
    assert state.outcome.diagnostic_code == "agent-timeout-evidence-accepted"
    assert state.outcome.terminal_evidence == "controlled-attempt-terminal"
    assert state.outcome.claim_authority == "diagnostic-only"
    assert state.outcome.no_retry is True
    assert state.artifact is not None
    assert state.artifact.terminal.state == "timed-out"
    assert state.artifact.attempt.retry_eligible is False
    assert state.artifact.attempt.resources.duration_milliseconds == 316_000
    assert state.diagnostic is not None
    assert state.diagnostic.diagnostic_code == "agent-timeout-evidence-accepted"
    diagnostic_bytes = (custody / "diagnostic.json").read_bytes()
    assert b"ordinary Harbor timeout diagnostics" not in diagnostic_bytes
    assert b"AgentTimeoutError" not in diagnostic_bytes
    assert containers.cleaned


def test_rejected_timeout_publishes_exact_value_free_mismatch_code(tmp_path: Path) -> None:
    plan, custody = _prepare(tmp_path)
    evaluation = tmp_path / "evaluation"
    evaluation.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=tmp_path,
        tasks=(plan.task,),
        evaluation_root=evaluation,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=AgentTimeoutResultProcess(
            plan.task, plan.specification, "wrong-timeout-message"
        ),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
        close_unusable_runtime_evidence=True,
        attempt_envelope_grace_seconds=60,
    )
    prepared = inspect_runtime_diagnostic_pilot(custody)

    state = step_runtime_diagnostic_pilot(
        custody,
        executor,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=prepared.request.request_id,
        wall_clock=lambda: 1001.0,
        disk_free=lambda _: FREE,
    )

    assert state.status == "completed"
    assert state.outcome is not None
    assert state.outcome.classification == "inconclusive"
    assert state.outcome.diagnostic_code == "agent-timeout-message"
    assert state.diagnostic is not None
    assert state.diagnostic.diagnostic_code == "agent-timeout-message"


class LifecycleExecutor:
    def __init__(self, *, retry_eligible: bool) -> None:
        self.retry_eligible = retry_eligible
        self.requests: list[ControlledAttemptRequest] = []

    @property
    def enforces_hard_deadline(self) -> bool:
        return True

    def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
        self.requests.append(request)
        return lifecycle_attempt(request, retry_eligible=self.retry_eligible)


def test_retry_eligible_result_still_stops_after_the_only_authorized_attempt(
    tmp_path: Path,
) -> None:
    plan, custody = _prepare(tmp_path)
    executor = LifecycleExecutor(retry_eligible=True)
    prepared = inspect_runtime_diagnostic_pilot(custody)

    first = step_runtime_diagnostic_pilot(
        custody,
        executor,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=prepared.request.request_id,
        wall_clock=lambda: 1001.0,
        disk_free=lambda _: FREE,
    )
    second = step_runtime_diagnostic_pilot(
        custody,
        executor,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=prepared.request.request_id,
        wall_clock=lambda: 1002.0,
        disk_free=lambda _: FREE,
    )

    assert len(executor.requests) == 1
    assert first == second
    assert second.outcome is not None
    assert second.outcome.classification == "inconclusive"
    assert second.outcome.no_retry is True
    assert second.artifact is not None and second.artifact.attempt.retry_eligible is True


def test_acceptance_must_match_both_exact_authorities_before_claim(tmp_path: Path) -> None:
    plan, custody = _prepare(tmp_path)
    executor = LifecycleExecutor(retry_eligible=False)
    prepared = inspect_runtime_diagnostic_pilot(custody)

    with pytest.raises(ContractError, match="acceptance does not equal"):
        step_runtime_diagnostic_pilot(
            custody,
            executor,
            accepted_plan_id="0" * 64,
            accepted_request_id=prepared.request.request_id,
            disk_free=lambda _: FREE,
        )

    state = inspect_runtime_diagnostic_pilot(custody)
    assert state.status == "prepared"
    assert executor.requests == []


def test_custody_cannot_be_prepared_without_exact_acceptance(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / f"runtime-diagnostic-{plan.plan_id}"
    request = build_runtime_diagnostic_authorization_request(plan, custody=custody)

    with pytest.raises(ContractError, match="acceptance and request"):
        prepare_runtime_diagnostic_pilot(
            plan,
            request,
            custody,
            accepted_plan_id=plan.plan_id,
            accepted_request_id="0" * 64,
            nonce="f" * 64,
            disk_free=lambda _: FREE,
        )

    assert not custody.exists()


def test_exclusive_claim_write_syncs_file_and_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced_modes: list[int] = []
    original_fsync = os.fsync

    def observe_fsync(descriptor: int) -> None:
        synced_modes.append(os.fstat(descriptor).st_mode)
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", observe_fsync)
    diagnostic_module._write_exclusive(tmp_path / "claim.json", b"{}\n")

    assert len(synced_modes) == 2
    assert stat.S_ISREG(synced_modes[0])
    assert stat.S_ISDIR(synced_modes[1])


def test_executor_crash_is_value_free_ambiguous_and_never_retried(tmp_path: Path) -> None:
    plan, custody = _prepare(tmp_path)
    prepared = inspect_runtime_diagnostic_pilot(custody)

    class CrashExecutor:
        enforces_hard_deadline = True
        calls = 0

        def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
            del request
            self.calls += 1
            raise RuntimeError("private provider detail")

    executor = CrashExecutor()
    with pytest.raises(AmbiguousRuntimeDiagnosticAttempt, match="without adoptable"):
        step_runtime_diagnostic_pilot(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=prepared.request.request_id,
            wall_clock=lambda: 1001.0,
            disk_free=lambda _: FREE,
        )

    state = inspect_runtime_diagnostic_pilot(custody)
    assert state.status == "ambiguous"
    assert state.incident is not None
    assert state.incident.category == "ambiguous-one-shot-attempt"
    assert b"private provider detail" not in (custody / "incident.json").read_bytes()
    with pytest.raises(AmbiguousRuntimeDiagnosticAttempt, match="cannot be retried"):
        step_runtime_diagnostic_pilot(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=prepared.request.request_id,
            disk_free=lambda _: FREE,
        )
    assert executor.calls == 1


def test_published_artifact_is_adopted_without_a_second_external_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, custody = _prepare(tmp_path)
    prepared = inspect_runtime_diagnostic_pilot(custody)
    executor = LifecycleExecutor(retry_eligible=False)
    original_write = diagnostic_module._write_exclusive
    failed = False

    def fail_first_outcome(path: Path, data: bytes) -> None:
        nonlocal failed
        if path.name == "outcome.json" and not failed:
            failed = True
            raise OSError("simulated outcome crash")
        original_write(path, data)

    monkeypatch.setattr(diagnostic_module, "_write_exclusive", fail_first_outcome)
    with pytest.raises(OSError, match="simulated outcome crash"):
        step_runtime_diagnostic_pilot(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=prepared.request.request_id,
            wall_clock=lambda: 1001.0,
            disk_free=lambda _: FREE,
        )
    assert inspect_runtime_diagnostic_pilot(custody).status == "adoptable"

    recovered = step_runtime_diagnostic_pilot(
        custody,
        executor,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=prepared.request.request_id,
        wall_clock=lambda: 1002.0,
        disk_free=lambda _: FREE,
    )

    assert recovered.status == "completed"
    assert len(executor.requests) == 1


def test_public_recovery_needs_no_auth_harbor_or_wheel_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    repository = tmp_path / "repository"
    custody_parent = repository / ".agent" / "custody"
    custody_parent.mkdir(parents=True)
    custody = custody_parent / f"runtime-diagnostic-{plan.plan_id}"
    request = build_runtime_diagnostic_authorization_request(plan, custody=custody)
    prepare_runtime_diagnostic_pilot(
        plan,
        request,
        custody,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=request.request_id,
        nonce="f" * 64,
        wall_clock=lambda: 1000.0,
        disk_free=lambda _: FREE,
    )
    executor = LifecycleExecutor(retry_eligible=False)
    original_write = diagnostic_module._write_exclusive
    failed = False

    def fail_first_outcome(path: Path, data: bytes) -> None:
        nonlocal failed
        if path.name == "outcome.json" and not failed:
            failed = True
            raise OSError("simulated outcome crash")
        original_write(path, data)

    monkeypatch.setattr(diagnostic_module, "_write_exclusive", fail_first_outcome)
    with pytest.raises(OSError, match="simulated outcome crash"):
        step_runtime_diagnostic_pilot(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=request.request_id,
            wall_clock=lambda: 1001.0,
            disk_free=lambda _: FREE,
        )

    recovered = diagnostic_module.step_runtime_diagnostic_pilot(
        custody,
        repository_root=repository,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=request.request_id,
    )
    repeated = diagnostic_module.step_runtime_diagnostic_pilot(
        custody,
        repository_root=repository,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=request.request_id,
    )

    assert recovered.status == "completed"
    assert repeated == recovered
    assert len(executor.requests) == 1


def test_wall_deadline_crossing_after_artifact_stops_then_adopts_without_retry(
    tmp_path: Path,
) -> None:
    plan, custody = _prepare(tmp_path)
    prepared = inspect_runtime_diagnostic_pilot(custody)
    executor = LifecycleExecutor(retry_eligible=False)
    readings = iter((0.0, 0.0, 0.0, 0.0, 901.0))

    with pytest.raises(diagnostic_module.RuntimeDiagnosticStopped, match="wall_deadline"):
        step_runtime_diagnostic_pilot(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=prepared.request.request_id,
            clock=lambda: next(readings),
            hard_deadline_monotonic=900.0,
            disk_free=lambda _: FREE,
        )

    assert inspect_runtime_diagnostic_pilot(custody).status == "adoptable"
    recovered = step_runtime_diagnostic_pilot(
        custody,
        executor,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=prepared.request.request_id,
        clock=lambda: 0.0,
        hard_deadline_monotonic=900.0,
        disk_free=lambda _: FREE,
    )
    assert recovered.status == "completed"
    assert len(executor.requests) == 1


def test_public_step_has_no_arbitrary_executor_injection_surface() -> None:
    import inspect

    parameters = inspect.signature(diagnostic_module.step_runtime_diagnostic_pilot).parameters

    assert "executor" not in parameters
    assert {"wall_clock", "clock", "disk_free"}.isdisjoint(parameters)
    assert {
        "repository_root",
        "cernora_wheel",
        "companion_wheel",
        "auth_file",
        "proxy_environment",
    }.issubset(parameters)


def test_closed_custody_rejects_unknown_files(tmp_path: Path) -> None:
    _, custody = _prepare(tmp_path)
    (custody / "unexpected.txt").write_text("not authority", encoding="utf-8")

    with pytest.raises(ContractError, match="unknown or missing"):
        inspect_runtime_diagnostic_pilot(custody)


def test_closed_custody_and_proposal_reject_unknown_empty_directories(tmp_path: Path) -> None:
    _, custody = _prepare(tmp_path)
    (custody / "empty-unknown").mkdir()
    with pytest.raises(ContractError, match="unknown or missing entries"):
        inspect_runtime_diagnostic_pilot(custody)

    companion, cernora = _candidate_wheels(tmp_path)
    source = _source_plan()
    repository = tmp_path / "repository-empty"
    (repository / ".agent" / "custody").mkdir(parents=True)
    proposal = tmp_path / "proposal-empty"
    create_runtime_diagnostic_proposal(
        proposal,
        source_plan=source,
        cernora_wheel=cernora,
        companion_wheel=companion,
        repository_root=repository,
    )
    (proposal / "empty-unknown").mkdir()
    with pytest.raises(ContractError, match="unknown or missing files"):
        inspect_runtime_diagnostic_proposal(proposal)


def test_offline_proposal_closes_exact_unapproved_one_shot_authority(tmp_path: Path) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    source = _source_plan()
    repository = tmp_path / "repository"
    (repository / ".agent" / "custody").mkdir(parents=True)
    proposal = tmp_path / "proposal"

    created = create_runtime_diagnostic_proposal(
        proposal,
        source_plan=source,
        cernora_wheel=cernora,
        companion_wheel=companion,
        repository_root=repository,
    )

    assert inspect_runtime_diagnostic_proposal(proposal) == created
    plan, request = created
    assert plan.execution_authorized is False
    assert plan.planned_trial_count == 1
    assert plan.maximum_attempt_count == 1
    assert plan.no_retry is True
    assert request.status == "awaiting-user-authorization"
    assert request.plan_id == plan.plan_id
    assert "second-attempt" in request.explicitly_not_authorized
    assert set(path.name for path in proposal.iterdir()) == {
        "plan.json",
        "request.json",
        "review.md",
    }


def test_runtime_attestation_binds_active_interpreter_to_proposed_wheels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    companion, cernora = _candidate_wheels(tmp_path)
    source = _source_plan()
    repository = tmp_path / "repository"
    (repository / ".agent" / "custody").mkdir(parents=True)
    proposal = tmp_path / "proposal"
    plan, _ = create_runtime_diagnostic_proposal(
        proposal,
        source_plan=source,
        cernora_wheel=cernora,
        companion_wheel=companion,
        repository_root=repository,
    )
    prefix = repository / ".venv"
    installed = prefix / "site-packages"
    installed.mkdir(parents=True)
    for wheel in (companion, cernora):
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(installed)

    class Distribution:
        def __init__(self, version: str) -> None:
            self.version = version

        def locate_file(self, path: str) -> Path:
            return installed / path

    versions = {"cernora": "0.1.4", "cernora-reference-workflow": "0.4.0"}
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(
        importlib.metadata,
        "distribution",
        lambda name: Distribution(versions[name]),
    )

    verify_runtime_diagnostic_runtime(
        plan,
        repository_root=repository,
        cernora_wheel=cernora,
        companion_wheel=companion,
    )

    changed = next(installed.glob("cernora_reference_workflow*/__init__.py"))
    changed.write_bytes(changed.read_bytes() + b"drift")
    with pytest.raises(ContractError, match="installed bytes changed"):
        verify_runtime_diagnostic_runtime(
            plan,
            repository_root=repository,
            cernora_wheel=cernora,
            companion_wheel=companion,
        )


def test_outcome_cannot_relabel_the_published_terminal_evidence(tmp_path: Path) -> None:
    plan, custody = _prepare(tmp_path)
    prepared = inspect_runtime_diagnostic_pilot(custody)
    state = step_runtime_diagnostic_pilot(
        custody,
        LifecycleExecutor(retry_eligible=False),
        accepted_plan_id=plan.plan_id,
        accepted_request_id=prepared.request.request_id,
        wall_clock=lambda: 1001.0,
        disk_free=lambda _: FREE,
    )
    assert state.outcome is not None and state.outcome.classification == "inconclusive"
    outcome_path = custody / "outcome.json"
    payload = json.loads(outcome_path.read_bytes())
    payload["classification"] = "timed-out"
    payload.pop("outcome_id")
    payload["outcome_id"] = canonical_content_id(payload, excluded=frozenset())
    outcome_path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(ContractError, match="contradicts terminal evidence"):
        inspect_runtime_diagnostic_pilot(custody)
