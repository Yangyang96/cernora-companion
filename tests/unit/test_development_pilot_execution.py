from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from cernora import BootstrapPlan

import cernora_reference_workflow.development_pilot_execution as pilot_execution_module
from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
)
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptRequest,
)
from cernora_reference_workflow.controlled_experiment_spec import materialize_authority_source
from cernora_reference_workflow.controlled_live_attempt import ControlledHarborAttemptExecutor
from cernora_reference_workflow.controlled_runtime import SubprocessResult
from cernora_reference_workflow.development_agent_pilot import (
    LEGACY_PILOT_CASE_IDS,
    LEGACY_PILOT_PROVIDER_SCOPE,
    PILOT_BASELINE_PROMPT_TEXT,
    PILOT_CASE_IDS,
    DevelopmentAgentPilotPlan,
    DevelopmentPilotCorpus,
    DevelopmentPilotImageSet,
    build_development_agent_pilot_plan,
    load_development_pilot_corpus,
    materialize_development_pilot_image_set,
)
from cernora_reference_workflow.development_pilot_bundle import (
    DevelopmentPilotAuthorizationRequest,
    _authorization_request,
)
from cernora_reference_workflow.development_pilot_execution import (
    AmbiguousDevelopmentPilotAttempt,
    DevelopmentPilotExecutionRecord,
    DevelopmentPilotIncidentReceipt,
    inspect_development_pilot_execution,
    prepare_development_pilot_execution,
    step_development_pilot_execution,
    summarize_development_pilot_execution,
)
from cernora_reference_workflow.m4_final_plan import build_controlled_specifications
from cernora_reference_workflow.study_preparation import ImplementationCandidate
from tests.unit.test_controlled_execution import lifecycle_attempt
from tests.unit.test_controlled_live_attempt import (
    FakeContainers,
    FakeProcess,
    _auth_file,
    _proxy_environment,
)
from tests.unit.test_study_execution import _evaluated_attempt

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "examples" / "priority4-development-pilot"
FREE = 20 * 1024**3


def _plan() -> DevelopmentAgentPilotPlan:
    corpus = load_development_pilot_corpus(CORPUS)
    images = materialize_development_pilot_image_set(
        build_base_image="cernora-reference/pi-runtime@sha256:" + "a" * 64,
        images={
            case_id: f"cernora-reference/p4-pilot-{case_id}@sha256:{index:064x}"
            for index, case_id in enumerate(PILOT_CASE_IDS, start=1)
        },
    )
    implementations = (
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
    )
    return build_development_agent_pilot_plan(
        corpus=corpus,
        images=images,
        implementation_candidates=implementations,
    )


_LEGACY_BASE = "cernora-reference/pi-runtime@sha256:" + "a" * 64


def _legacy_corpus_and_images() -> tuple[DevelopmentPilotCorpus, DevelopmentPilotImageSet]:
    corpus = load_development_pilot_corpus(CORPUS)
    corpus_payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-corpus/v1",
        "tasks": [
            item.model_dump(mode="json")
            for item in corpus.tasks
            if item.case.case_id in LEGACY_PILOT_CASE_IDS
        ],
        "calibrations": [
            item.model_dump(mode="json")
            for item in corpus.calibrations
            if item.case_id in LEGACY_PILOT_CASE_IDS
        ],
    }
    corpus_payload["corpus_id"] = canonical_content_id(corpus_payload, excluded=frozenset())
    images_payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-images/v1",
        "build_base_image": _LEGACY_BASE,
        "platform": "linux/arm64",
        "images": [
            {
                "case_id": case_id,
                "image": f"cernora-reference/p4-pilot-{case_id}@sha256:{index:064x}",
            }
            for index, case_id in enumerate(LEGACY_PILOT_CASE_IDS, start=1)
        ],
    }
    images_payload["image_set_id"] = canonical_content_id(images_payload, excluded=frozenset())
    return (
        DevelopmentPilotCorpus.model_validate(corpus_payload),
        DevelopmentPilotImageSet.model_validate(images_payload),
    )


def _legacy_plan() -> DevelopmentAgentPilotPlan:
    corpus, images = _legacy_corpus_and_images()
    baseline = materialize_authority_source(
        "p4-confirmatory-baseline-prompt-v1", {"text": PILOT_BASELINE_PROMPT_TEXT}
    )
    specs = build_controlled_specifications(
        tasks=corpus.tasks,
        images={item.case_id: item.image for item in images.images},
        build_base_image=images.build_base_image,
        configurations=(("baseline", baseline),),
        bootstrap=BootstrapPlan(
            method="case-clustered-paired-bootstrap/v1",
            confidence_basis_points=9500,
            resamples=10000,
            percentile="nearest_rank_closed",
            seed_source="comparison_input_sha256",
        ),
        pass_k=None,
        timeout_seconds=300,
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-agent-pilot-plan/v1",
        "selected_study_mode": "confirmatory-effect",
        "authority_scope": "development-only-agent-pilot",
        "execution_authorized": False,
        "treatment_axis_if_eligible": "prompt-instruction",
        "corpus": corpus.model_dump(mode="json"),
        "images": images.model_dump(mode="json"),
        "baseline_prompt": baseline.model_dump(mode="json"),
        "connector": {
            "connector_id": "cernora-reference-harbor-pi",
            "connector_version": "2",
            "platform_qualification": "macos-arm64",
        },
        "experiment_specs": [item.model_dump(mode="json") for item in specs],
        "repetitions": 1,
        "planned_trial_count": 6,
        "worst_case_attempt_count": 12,
        "execution": {
            "concurrency": 1,
            "max_attempt_count": 12,
            "max_total_wall_time_seconds": 7200,
            "token_budget": {
                "status": "unavailable",
                "reason": "no-structured-authoritative-source",
            },
            "monetary_budget": {
                "status": "unavailable",
                "reason": "no-structured-authoritative-source",
            },
        },
        "preflight_free_bytes": 16106127360,
        "safe_stop_free_bytes": 8589934592,
        "external_provider_scope": LEGACY_PILOT_PROVIDER_SCOPE,
        "custody_policy": "new-durable-git-ignored-directory",
        "stop_policy": {
            "no_behavioral_failure": "stop-no-candidate",
            "incomplete_or_missing_evidence": "stop-inconclusive",
            "ambiguous_active_attempt": "pause-no-retry",
            "completion": "stop-before-candidate-construction",
        },
        "prohibited_actions": [
            "held-out-access",
            "smoke-execution",
            "study-start-execution",
            "study-step-execution",
            "54-trial-matrix",
        ],
    }
    payload["plan_id"] = canonical_content_id(payload, excluded=frozenset())
    return DevelopmentAgentPilotPlan.model_validate(payload)


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


def _request(
    plan: DevelopmentAgentPilotPlan, custody: Path
) -> DevelopmentPilotAuthorizationRequest:
    payload = _authorization_request(plan, repository_root=ROOT).model_dump(mode="json")
    payload["custody_path_sha256"] = pilot_execution_module._custody_path_sha256(
        custody, must_exist=False
    )
    payload.pop("request_id")
    payload["request_id"] = canonical_content_id(payload, excluded=frozenset())
    return DevelopmentPilotAuthorizationRequest.model_validate(payload)


def _prepare(plan: DevelopmentAgentPilotPlan, custody: Path) -> None:
    result = prepare_development_pilot_execution(
        plan,
        custody,
        authorization_request=_request(plan, custody),
        nonce="f" * 64,
        wall_clock=lambda: 1000.0,
        disk_free=lambda _: FREE,
    )
    assert result.status == "prepared"


def _request_id(custody: Path) -> str:
    request_id = inspect_development_pilot_execution(custody).record.authorization_request_id
    assert request_id is not None
    return request_id


def test_each_step_claims_at_most_one_attempt_and_all_passes_stop_no_candidate(
    tmp_path: Path,
) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    executor = EvaluatedExecutor(plan, tmp_path / "evaluations")

    for index in range(len(PILOT_CASE_IDS)):
        result = step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=_request_id(custody),
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
    assert len(state.outcome.observations) == len(PILOT_CASE_IDS)
    assert all(item.agent_outcome == "pass" for item in state.outcome.observations)
    assert state.attempt_count == len(PILOT_CASE_IDS)


def test_prepared_custody_is_not_reported_as_running(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)

    summary = summarize_development_pilot_execution(custody)

    assert summary.status == "prepared"
    assert summary.attempt_count == 0
    assert summary.completed_trial_count == 0


def test_core_prepare_rejects_historical_unbound_plan(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="current Plan v4"):
        prepare_development_pilot_execution(
            _legacy_plan(),
            tmp_path / "legacy-custody",
            authorization_request=_authorization_request(_plan(), repository_root=ROOT),
            disk_free=lambda _: FREE,
        )


def test_core_step_rejects_historical_unbound_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custody = tmp_path / "legacy-custody"
    custody.mkdir()
    (custody / ".writer.lock").write_bytes(b"")
    monkeypatch.setattr(
        pilot_execution_module,
        "inspect_development_pilot_execution",
        lambda _: SimpleNamespace(plan=_legacy_plan()),
    )
    executor = CrashingExecutor()

    with pytest.raises(ContractError, match="current Plan v4"):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=_legacy_plan().plan_id,
            accepted_request_id="0" * 64,
            disk_free=lambda _: FREE,
        )

    assert executor.requests == []


def test_legacy_custody_rejects_posthoc_diagnostics_directory(tmp_path: Path) -> None:
    custody = tmp_path / "legacy-custody"
    custody.mkdir()
    (custody / ".writer.lock").write_bytes(b"")
    (custody / "artifacts").mkdir()
    (custody / "diagnostics").mkdir()
    (custody / "ledger").mkdir()
    legacy = _legacy_plan()
    record_payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-execution/v1",
        "plan_id": legacy.plan_id,
        "nonce": "a" * 64,
        "prepared_unix_milliseconds": 1,
    }
    record_payload["execution_id"] = canonical_content_id(
        {"nonce": record_payload["nonce"], "plan_id": record_payload["plan_id"]},
        excluded=frozenset(),
    )
    record = DevelopmentPilotExecutionRecord.model_validate(record_payload)
    (custody / "plan.json").write_bytes(legacy.canonical_bytes())
    (custody / "record.json").write_bytes(record.canonical_bytes())

    with pytest.raises(ContractError, match="legacy.*cannot contain diagnostics"):
        inspect_development_pilot_execution(custody)


def test_cli_rejects_copied_custody_before_runtime_or_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    copied = tmp_path / "copied-custody"
    _prepare(plan, copied)
    repository = tmp_path / "repository"
    repository.mkdir()

    runtime_verified = False

    def forbidden_runtime(*_args: object, **_kwargs: object) -> None:
        nonlocal runtime_verified
        runtime_verified = True

    script_spec = importlib.util.spec_from_file_location(
        "development_pilot_cli_under_test",
        ROOT / "scripts/run_development_agent_pilot.py",
    )
    assert script_spec is not None and script_spec.loader is not None
    pilot_cli = importlib.util.module_from_spec(script_spec)
    script_spec.loader.exec_module(pilot_cli)
    monkeypatch.setattr(pilot_cli, "verify_development_pilot_runtime", forbidden_runtime)
    step = pilot_cli._step

    with pytest.raises(ContractError, match="exact authorized custody path"):
        step(
            copied,
            repository,
            plan.plan_id,
            _request_id(copied),
            tmp_path / "missing-companion.whl",
            tmp_path / "missing-core.whl",
        )

    assert runtime_verified is False
    assert inspect_development_pilot_execution(copied).attempt_count == 0


def test_execution_record_rejects_byte_copied_custody(tmp_path: Path) -> None:
    plan = _plan()
    original = tmp_path / "original"
    copied = tmp_path / "copied"
    _prepare(plan, original)
    shutil.copytree(original, copied)

    with pytest.raises(ContractError, match="another custody path"):
        inspect_development_pilot_execution(copied)


def test_canonical_request_rejects_self_consistent_custody_rewrite(tmp_path: Path) -> None:
    plan = _plan()
    original = tmp_path / "original"
    copied = tmp_path / "copied"
    _prepare(plan, original)
    shutil.copytree(original, copied)

    record_path = copied / "record.json"
    record = json.loads(record_path.read_bytes())
    record["custody_path_sha256"] = pilot_execution_module._custody_path_sha256(
        copied, must_exist=True
    )
    record.pop("execution_id")
    record["execution_id"] = canonical_content_id(
        {
            "authorization_request_id": record["authorization_request_id"],
            "custody_path_sha256": record["custody_path_sha256"],
            "nonce": record["nonce"],
            "plan_id": record["plan_id"],
        },
        excluded=frozenset(),
    )
    record_path.write_bytes(canonical_json_bytes(record))

    prepared_path = copied / "ledger" / "000001.json"
    prepared = json.loads(prepared_path.read_bytes())
    prepared["execution_id"] = record["execution_id"]
    prepared.pop("entry_id")
    prepared["entry_id"] = canonical_content_id(prepared, excluded=frozenset())
    prepared_path.write_bytes(canonical_json_bytes(prepared))

    with pytest.raises(ContractError, match="authorization request contradicts custody"):
        inspect_development_pilot_execution(copied)


def test_core_prepare_rejects_same_request_at_another_custody(tmp_path: Path) -> None:
    plan = _plan()
    authorized = tmp_path / "authorized"
    duplicate = tmp_path / "duplicate"
    request = _request(plan, authorized)
    prepare_development_pilot_execution(
        plan,
        authorized,
        authorization_request=request,
        nonce="e" * 64,
        disk_free=lambda _: FREE,
    )

    with pytest.raises(ContractError, match="does not authorize this custody path"):
        prepare_development_pilot_execution(
            plan,
            duplicate,
            authorization_request=request,
            nonce="d" * 64,
            disk_free=lambda _: FREE,
        )


def test_core_prepare_rejects_request_with_different_case_authority(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    payload = _request(plan, custody).model_dump(mode="json")
    payload["case_authority_sha256"][0] = "d" * 64
    payload.pop("request_id")
    payload["request_id"] = canonical_content_id(payload, excluded=frozenset())
    contradictory = DevelopmentPilotAuthorizationRequest.model_validate(payload)

    with pytest.raises(ContractError, match="does not authorize this custody path"):
        prepare_development_pilot_execution(
            plan,
            custody,
            authorization_request=contradictory,
            nonce="d" * 64,
            disk_free=lambda _: FREE,
        )


def test_core_step_requires_exact_request_acceptance(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    executor = CrashingExecutor()

    with pytest.raises(ContractError, match="exact request ID"):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id="0" * 64,
            disk_free=lambda _: FREE,
        )

    assert executor.requests == []


def test_core_rejects_symlink_custody_alias_and_parent(tmp_path: Path) -> None:
    plan = _plan()
    original = tmp_path / "original"
    _prepare(plan, original)
    alias = tmp_path / "alias"
    alias.symlink_to(original, target_is_directory=True)

    with pytest.raises(ContractError, match="non-symlink ancestry"):
        inspect_development_pilot_execution(alias)

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    parent_alias = tmp_path / "parent-alias"
    parent_alias.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ContractError, match="non-symlink ancestry"):
        prepare_development_pilot_execution(
            plan,
            parent_alias / "custody",
            authorization_request=_authorization_request(plan, repository_root=ROOT),
            disk_free=lambda _: FREE,
        )


def test_cli_rejects_symlinked_custody_ancestor(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / ".agent").symlink_to(outside, target_is_directory=True)
    script_spec = importlib.util.spec_from_file_location(
        "development_pilot_cli_symlink_test",
        ROOT / "scripts/run_development_agent_pilot.py",
    )
    assert script_spec is not None and script_spec.loader is not None
    pilot_cli = importlib.util.module_from_spec(script_spec)
    script_spec.loader.exec_module(pilot_cli)
    ensure_parent = pilot_cli._ensure_custody_parent

    with pytest.raises(ContractError, match="ancestors must be real directories"):
        ensure_parent(repository)

    assert not (outside / "custody").exists()


def test_custody_rejects_orphan_artifact_entries(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    orphan = custody / "artifacts" / "orphan"
    orphan.write_bytes(b"not an artifact")

    with pytest.raises(ContractError, match="unknown entry"):
        inspect_development_pilot_execution(custody)

    orphan.unlink()
    orphan.mkdir()
    with pytest.raises(ContractError, match="omits a real manifest"):
        inspect_development_pilot_execution(custody)


def test_exact_orphan_artifact_is_adopted_without_rerunning_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    executor = EvaluatedExecutor(plan, tmp_path / "evaluations")
    append_entry = pilot_execution_module._append_entry

    def lose_publication(root: Path, entry: object) -> None:
        if getattr(entry, "event", None) == "attempt-published":
            raise OSError("simulated ledger append loss")
        append_entry(root, entry)  # type: ignore[arg-type]

    monkeypatch.setattr(pilot_execution_module, "_append_entry", lose_publication)
    with pytest.raises(OSError, match="append loss"):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=_request_id(custody),
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            disk_free=lambda _: FREE,
        )
    monkeypatch.setattr(pilot_execution_module, "_append_entry", append_entry)
    state = inspect_development_pilot_execution(custody)
    assert state.adoptable_artifact is not None

    forbidden = CrashingExecutor()
    result = step_development_pilot_execution(
        custody,
        forbidden,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=_request_id(custody),
        wall_clock=lambda: 1000.0,
        clock=lambda: 0.0,
        disk_free=lambda _: FREE,
    )

    assert forbidden.requests == []
    assert result.attempt_count == 1
    assert inspect_development_pilot_execution(custody).adoptable_artifact is None


def test_executor_failure_writes_value_free_incident_and_keeps_claim_ambiguous(
    tmp_path: Path,
) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)

    with pytest.raises(RuntimeError, match="simulated process loss"):
        step_development_pilot_execution(
            custody,
            CrashingExecutor(),
            accepted_plan_id=plan.plan_id,
            accepted_request_id=_request_id(custody),
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            disk_free=lambda _: FREE,
        )

    state = inspect_development_pilot_execution(custody)
    assert state.ambiguous_claim is not None
    assert len(state.incidents) == 1
    assert state.incidents[0].category == "unexpected-executor-error"
    serialized = next((custody / "diagnostics").iterdir()).read_bytes()
    assert b"simulated process loss" not in serialized


def test_post_executor_validation_failure_writes_incident(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)

    class WrongRequestExecutor(EvaluatedExecutor):
        def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
            attempt = super().__call__(request)
            return attempt.model_copy(update={"trial_id": "0" * 64})

    with pytest.raises(ContractError, match="another Attempt request"):
        step_development_pilot_execution(
            custody,
            WrongRequestExecutor(plan, tmp_path / "evaluations"),
            accepted_plan_id=plan.plan_id,
            accepted_request_id=_request_id(custody),
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            disk_free=lambda _: FREE,
        )

    state = inspect_development_pilot_execution(custody)
    assert state.ambiguous_claim is not None
    assert len(state.incidents) == 1
    assert state.incidents[0].phase == "attempt-validation"


def test_incident_cannot_bind_a_published_claim_or_predate_it(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    step_development_pilot_execution(
        custody,
        EvaluatedExecutor(plan, tmp_path / "evaluations"),
        accepted_plan_id=plan.plan_id,
        accepted_request_id=_request_id(custody),
        wall_clock=lambda: 1000.0,
        clock=lambda: 0.0,
        disk_free=lambda _: FREE,
    )
    state = inspect_development_pilot_execution(custody)
    claim = next(item for item in state.entries if item.event == "attempt-claimed")
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-incident/v1",
        "execution_id": state.record.execution_id,
        "plan_id": plan.plan_id,
        "claim_entry_id": claim.entry_id,
        "phase": "executor",
        "category": "unexpected-executor-error",
        "observed_unix_milliseconds": 0,
    }
    payload["incident_id"] = canonical_content_id(payload, excluded=frozenset())
    receipt = DevelopmentPilotIncidentReceipt.model_validate(payload)
    (custody / "diagnostics" / f"{claim.entry_id}.json").write_bytes(receipt.canonical_bytes())

    with pytest.raises(ContractError, match="incident contradicts custody"):
        inspect_development_pilot_execution(custody)


def test_timed_out_live_executor_closes_claim_as_published_lifecycle(
    tmp_path: Path,
) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    task = plan.corpus.tasks[0]
    specification = plan.experiment_specs[0]

    class TimedOutProcess(FakeProcess):
        def __call__(self, *args: object, **kwargs: object) -> SubprocessResult:
            result = super().__call__(*args, **kwargs)  # type: ignore[arg-type]
            return SubprocessResult(
                status="timed_out",
                exit_code=None,
                stdout=result.stdout,
                stderr=result.stderr,
                started_monotonic=result.started_monotonic,
                finished_monotonic=result.finished_monotonic,
                receipt_sha256=result.receipt_sha256,
            )

    repository = tmp_path / "repository"
    repository.mkdir()
    evaluation = tmp_path / "live-evaluation"
    evaluation.mkdir()
    containers = FakeContainers()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository,
        tasks=plan.corpus.tasks,
        evaluation_root=evaluation,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=TimedOutProcess(task, specification),
        container_controller=containers,
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
        close_unusable_runtime_evidence=True,
        attempt_envelope_grace_seconds=60,
    )

    result = step_development_pilot_execution(
        custody,
        executor,
        accepted_plan_id=plan.plan_id,
        accepted_request_id=_request_id(custody),
        wall_clock=lambda: 1000.0,
        clock=lambda: 0.0,
        disk_free=lambda _: FREE,
    )

    state = inspect_development_pilot_execution(custody)
    assert result.attempt_count == 1
    assert state.ambiguous_claim is None
    assert len(state.artifacts) == 1
    assert state.artifacts[0].attempt.lifecycle is not None
    assert state.artifacts[0].attempt.lifecycle.category == "runtime_pre_terminal_failure"
    assert state.artifacts[0].attempt.retry_eligible is False
    assert containers.cleaned


def test_publication_time_is_clamped_when_wall_clock_moves_backward(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    times = iter((1000.0, 999.0))

    step_development_pilot_execution(
        custody,
        EvaluatedExecutor(plan, tmp_path / "evaluations"),
        accepted_plan_id=plan.plan_id,
        accepted_request_id=_request_id(custody),
        wall_clock=lambda: next(times),
        clock=lambda: 0.0,
        disk_free=lambda _: FREE,
    )

    state = inspect_development_pilot_execution(custody)
    claim = next(item for item in state.entries if item.event == "attempt-claimed")
    publication = next(item for item in state.entries if item.event == "attempt-published")
    assert publication.observed_unix_milliseconds == claim.observed_unix_milliseconds
    assert publication.elapsed_milliseconds >= 0


def test_next_step_time_is_clamped_to_latest_publication(tmp_path: Path) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    first_times = iter((1000.0, 1010.0))
    step_development_pilot_execution(
        custody,
        EvaluatedExecutor(plan, tmp_path / "evaluations-first"),
        accepted_plan_id=plan.plan_id,
        accepted_request_id=_request_id(custody),
        wall_clock=lambda: next(first_times),
        clock=lambda: 0.0,
        disk_free=lambda _: FREE,
    )

    second_times = iter((1005.0, 1005.0))
    step_development_pilot_execution(
        custody,
        EvaluatedExecutor(plan, tmp_path / "evaluations-second"),
        accepted_plan_id=plan.plan_id,
        accepted_request_id=_request_id(custody),
        wall_clock=lambda: next(second_times),
        clock=lambda: 0.0,
        disk_free=lambda _: FREE,
    )

    state = inspect_development_pilot_execution(custody)
    observed = tuple(item.observed_unix_milliseconds for item in state.entries)
    assert observed == tuple(sorted(observed))
    assert state.attempt_count == 2


def test_real_evaluated_failure_is_selected_without_heldout_or_fabrication(
    tmp_path: Path,
) -> None:
    plan = _plan()
    custody = tmp_path / "custody"
    _prepare(plan, custody)
    failing = frozenset({"p4-dev-json-pointer"})
    executor = EvaluatedExecutor(plan, tmp_path / "evaluations", failing_cases=failing)

    for _ in range(len(PILOT_CASE_IDS)):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=_request_id(custody),
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
    assert failures[0].case_id == "p4-dev-json-pointer"
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
            accepted_request_id=_request_id(custody),
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
            accepted_request_id=_request_id(custody),
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
            accepted_request_id=_request_id(custody),
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

    for _ in range(len(PILOT_CASE_IDS)):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=_request_id(custody),
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            disk_free=lambda _: FREE,
        )

    outcome = inspect_development_pilot_execution(custody).outcome
    assert outcome is not None
    assert outcome.status == "inconclusive"
    assert outcome.leading_failure_code is None
    assert len(outcome.observations) == len(PILOT_CASE_IDS) - 1


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

    for _ in range(len(PILOT_CASE_IDS) + 1):
        step_development_pilot_execution(
            custody,
            executor,
            accepted_plan_id=plan.plan_id,
            accepted_request_id=_request_id(custody),
            wall_clock=lambda: 1000.0,
            clock=lambda: 0.0,
            sleeper=sleeps.append,
            disk_free=lambda _: FREE,
        )

    state = inspect_development_pilot_execution(custody)
    assert state.outcome is not None
    assert state.outcome.status == "no-candidate"
    assert state.attempt_count == len(PILOT_CASE_IDS) + 1
    assert len(state.attempts_by_slot[0]) == 2
    assert sleeps == [10.0]
