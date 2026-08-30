from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from cernora import (
    BatchAttempt,
    BatchAttemptResources,
    BatchPlannedTrial,
    BatchSummaryPackage,
    BatchTrial,
    build_batch_summary,
    materialize_batch_input,
)

import cernora_reference_workflow.improvement_loop as improvement_loop_module
from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    materialize_comparison_plan,
    materialize_treatment_declaration,
)
from cernora_reference_workflow.controlled_evaluation import materialize_repair_result
from cernora_reference_workflow.controlled_experiment_spec import (
    materialize_authority_source,
    materialize_controlled_experiment_spec,
    materialize_dataset_authority,
)
from cernora_reference_workflow.controlled_profile import (
    PROFILE_ID,
    PROFILE_VERSION,
    evaluate_repair_result_package,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
    materialize_controlled_task,
    reconstructed_revealed_case,
    task_from_revealed_case,
)
from cernora_reference_workflow.heldout_seal import (
    HeldoutArchiveCase,
    HeldoutManifest,
    HeldoutRevealReceipt,
)
from cernora_reference_workflow.improvement_loop import (
    CandidateFreeze,
    assemble_final_comparison_input,
    derive_candidate_freeze,
    verify_candidate_freeze,
    visible_corpus_digest,
)
from tests.unit.test_controlled_run_plan import valid_m4_payload

VISIBLE_ROOT = Path("examples/m4-visible")
DEV_NAMES = ("dev-integer-ledger", "dev-interval-merge", "dev-query-codec")
REG_NAMES = ("reg-csv-rollup", "reg-graph-topology", "reg-posix-normalization")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _tasks(names: tuple[str, ...]) -> tuple[ControlledTaskAuthority, ...]:
    return tuple(load_visible_task(VISIBLE_ROOT / item) for item in names)


def _pilot(tmp_path: Path, *, pass_all: bool = False) -> BatchSummaryPackage:
    tasks = _tasks(DEV_NAMES)
    run_plan_id = digest("offline-development-pilot-run-plan")
    execution_id = digest("offline-development-pilot-execution")
    planned: list[BatchPlannedTrial] = []
    trials: list[BatchTrial] = []
    for index, task in enumerate(tasks, start=1):
        source_attempt_id = digest(f"pilot-source-attempt:{task.case.case_id}:{pass_all}")
        result = materialize_repair_result(
            {
                "schema_version": "cernora.reference.repair-result/v1",
                "case_id": task.case.case_id,
                "result_record_version": "agent.evaluator.result-record/v1",
                "test_authority_sha256": digest("pilot-test-authority"),
                "test_plan_sha256": digest("pilot-test-plan"),
                "test_source_sha256": task.test_source_sha256,
                "termination": "exited",
                "exit_code": 0 if pass_all else 1,
                "checks": [
                    {
                        "check_id": "frozen-verifier",
                        "failure_code": task.failure_code,
                        "passed": pass_all,
                    }
                ],
                "allowed_paths": list(task.allowed_paths),
                "changed_paths": [],
                "protected_paths": list(task.protected_paths),
                "protected_path_receipt": {
                    "before_sha256": digest(f"protected:{task.case.case_id}"),
                    "after_sha256": digest(f"protected:{task.case.case_id}"),
                    "unchanged": True,
                },
            }
        )
        package = evaluate_repair_result_package(
            task=task,
            tasks=tasks,
            result=result,
            source_attempt_id=source_attempt_id,
            output=tmp_path / f"pilot-evaluation-{index}",
        )
        trial_slot_id = digest(f"pilot-slot:{task.case.case_id}")
        trial_id = digest(f"pilot-trial:{task.case.case_id}")
        experiment_id = digest(f"pilot-experiment:{task.case.case_id}")
        attempt_id = digest(f"pilot-attempt:{task.case.case_id}:{pass_all}")
        planned.append(
            BatchPlannedTrial(
                slot_index=index,
                trial_slot_id=trial_slot_id,
                case_id=task.case.case_id,
                configuration_id="baseline",
                experiment_id=experiment_id,
                repetition=1,
            )
        )
        attempt = BatchAttempt(
            schema_version="agent.evaluator.batch-attempt/v1",
            attempt_id=attempt_id,
            source_attempt_id=source_attempt_id,
            trial_id=trial_id,
            ordinal=1,
            predecessor_attempt_id=None,
            source_manifest_sha256=digest(f"pilot-manifest:{task.case.case_id}"),
            retry_eligible=False,
            resources=BatchAttemptResources(),
            evaluation=package,
            lifecycle=None,
        )
        trials.append(
            BatchTrial(
                schema_version="agent.evaluator.batch-trial/v1",
                run_plan_id=run_plan_id,
                execution_id=execution_id,
                trial_id=trial_id,
                slot_index=index,
                trial_slot_id=trial_slot_id,
                case_id=task.case.case_id,
                configuration_id="baseline",
                experiment_id=experiment_id,
                repetition=1,
                selected_attempt_id=attempt_id,
                attempts=(attempt,),
            )
        )
    batch = materialize_batch_input(
        {
            "schema_version": "agent.evaluator.batch-input/v1",
            "run_plan_id": run_plan_id,
            "execution_id": execution_id,
            "execution_status": "completed",
            "budget_status": "within_budget",
            "companion_version": "0.4.0-offline-pilot",
            "planned_trial_count": 3,
            "attempt_count": 3,
            "planned_trials": [item.model_dump(mode="json") for item in planned],
            "trials": [item.model_dump(mode="json") for item in trials],
        }
    )
    return BatchSummaryPackage(batch_input=batch, summary=build_batch_summary(batch))


def _manifest() -> HeldoutManifest:
    source = HeldoutManifest.from_file(Path("examples/m4-heldout-sealed/manifest.json"))
    heldout = tuple(task for task in _all_task_authorities(source) if task.split_id == "held-out")
    cases = [_revealed_case_authority(task) for task in heldout]
    commitments = [
        {
            "case_id": task.case.case_id,
            "plaintext_sha256": sha256_bytes(canonical_json_bytes(case)),
        }
        for task, case in zip(heldout, cases, strict=True)
    ]
    suite = {
        "schema_version": "cernora.reference.heldout-suite-commitment/v1",
        "cases": commitments,
    }
    archive = {
        "schema_version": "cernora.reference.heldout-archive/v1",
        "cases": cases,
    }
    payload = source.model_dump(mode="json", exclude={"manifest_id"})
    payload["case_commitments"] = commitments
    payload["suite_sha256"] = sha256_bytes(canonical_json_bytes(suite))
    payload["archive_sha256"] = sha256_bytes(canonical_json_bytes(archive))
    aad = {
        "schema_version": "cernora.reference.heldout-seal-aad/v1",
        "algorithm": payload["algorithm"],
        "case_commitments": commitments,
        "suite_sha256": payload["suite_sha256"],
        "archive_sha256": payload["archive_sha256"],
        "nonce": payload["nonce"],
    }
    payload["aad_sha256"] = sha256_bytes(canonical_json_bytes(aad))
    payload["manifest_id"] = canonical_content_id(payload, excluded=frozenset())
    return HeldoutManifest.model_validate(payload)


def _generic_heldout_case(case_id: str, template: ControlledTaskAuthority) -> HeldoutArchiveCase:
    files = (*template.workspace_files, *template.test_files)
    return HeldoutArchiveCase(
        case_id=case_id,
        task={
            "schema_version": "cernora.reference.heldout-task/v1",
            "language": "python",
            "instruction": template.case.input.prompt,
            "allowed_paths": list(template.allowed_paths),
            "protected_paths": list(template.protected_paths),
            "case_version": template.case.case_version,
        },
        workspace={
            "schema_version": "cernora.reference.heldout-workspace/v1",
            "files": [
                {"path": item.path, "content_utf8": item.content().decode("utf-8")}
                for item in files
            ],
        },
        evaluation={
            "schema_version": "cernora.reference.heldout-evaluation/v1",
            "command": list(template.test_command),
            "working_directory": ".",
            "timeout_seconds": 60,
            "network": "disabled",
            "expected_exit_code": 0,
            "success_metric": "verifier_exit_zero",
            "failure_codes": [template.failure_code],
        },
    )


def _revealed_case_authority(task: ControlledTaskAuthority) -> dict[str, object]:
    return reconstructed_revealed_case(task).model_dump(mode="json")


def _all_task_authorities(manifest: HeldoutManifest) -> tuple[ControlledTaskAuthority, ...]:
    visible = _tasks((*DEV_NAMES, *REG_NAMES))
    template = visible[0]
    heldout = tuple(
        task_from_revealed_case(_generic_heldout_case(commitment.case_id, template))
        for commitment in manifest.case_commitments
    )
    return (*visible, *heldout)


def _final_plan(
    manifest: HeldoutManifest,
    *,
    candidate_prompt: str = "Inspect the leading failure, then repair the project.",
    task_authorities: tuple[ControlledTaskAuthority, ...] | None = None,
) -> ControlledRunPlanV2:
    from tests.unit.test_controlled_live_attempt import _spec

    tasks = tuple(
        sorted(
            _all_task_authorities(manifest) if task_authorities is None else task_authorities,
            key=lambda item: item.case.case_id,
        )
    )
    failure_binding = {
        "code": "interval_boundary_v1",
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
    }
    raw_specs = tuple(
        _spec(
            task,
            configuration_id=configuration_id,
            prompt=prompt,
            candidate_failure_binding=(
                failure_binding if configuration_id == "candidate" else None
            ),
            profile_tasks=tasks,
        )
        for task in tasks
        for configuration_id, prompt in (
            ("baseline", "Repair the project."),
            ("candidate", candidate_prompt),
        )
    )
    dataset = materialize_dataset_authority(
        tuple(item.dataset_authority.cases[0] for item in raw_specs[::2])
    )
    specs = []
    for item in raw_specs:
        payload = item.model_dump(mode="json", exclude={"experiment_id"})
        payload["dataset_authority"] = dataset.model_dump(mode="json")
        specs.append(materialize_controlled_experiment_spec(payload))
    plan_payload = valid_m4_payload(case_ids=tuple(task.case.case_id for task in tasks))
    plan_payload["experiment_specs"] = [item.model_dump(mode="json") for item in specs]
    plan_payload["cases"] = [
        {
            "case_id": item.task.task_id,
            "case_version": item.task.task_version,
            "task_content_sha256": item.task.content_sha256,
        }
        for item in specs[::2]
    ]
    plan_payload["cells"] = [
        {
            "case_id": item.task.task_id,
            "configuration_id": item.configuration_id,
            "experiment_id": item.experiment_id,
        }
        for item in specs
    ]
    return materialize_controlled_run_plan(plan_payload)


def _comparison(plan: ControlledRunPlanV2, manifest: HeldoutManifest) -> ComparisonPlanV1:
    split_by_case = {
        **{item: "development" for item in DEV_NAMES},
        **{item: "regression" for item in REG_NAMES},
        **{item.case_id: "held-out" for item in manifest.case_commitments},
    }
    statistics = plan.experiment_specs[0].statistical_policy
    return materialize_comparison_plan(
        {
            "schema_version": "cernora.reference.comparison-plan/v1",
            "source_run_plan_id": plan.run_plan_id,
            "baseline_configuration_id": "baseline",
            "candidate_configuration_id": "candidate",
            "case_splits": [
                {"case_id": item.case_id, "split_id": split_by_case[item.case_id]}
                for item in plan.cases
            ],
            "treatment": materialize_treatment_declaration(("prompt_instruction",)).model_dump(
                mode="json"
            ),
            "primary_outcome": {
                "metric": "reliable_success_rate",
                "scope": "split",
                "split_id": "held-out",
                "direction": "higher_is_better",
                "practical_threshold_basis_points": 1000,
            },
            "guardrails": [
                {
                    "guardrail_id": "evaluation-validity",
                    "hard": True,
                    "metric": "evaluation_validity_rate",
                    "scope": "all",
                    "split_id": None,
                    "direction": "higher_is_better",
                    "max_adverse_basis_points": 0,
                    "profile_id": None,
                    "profile_version": None,
                    "failure_code": None,
                },
                {
                    "guardrail_id": "protected-paths",
                    "hard": True,
                    "metric": "profile_failure_code_rate",
                    "scope": "all",
                    "split_id": None,
                    "direction": "lower_is_better",
                    "max_adverse_basis_points": 0,
                    "profile_id": PROFILE_ID,
                    "profile_version": PROFILE_VERSION,
                    "failure_code": "protected_paths_unchanged_v1",
                },
                {
                    "guardrail_id": "regression-rsr",
                    "hard": True,
                    "metric": "reliable_success_rate",
                    "scope": "split",
                    "split_id": "regression",
                    "direction": "higher_is_better",
                    "max_adverse_basis_points": 1000,
                    "profile_id": None,
                    "profile_version": None,
                    "failure_code": None,
                },
            ],
            "bootstrap": statistics.bootstrap.model_dump(mode="json"),
            "pass_k": (
                None if statistics.pass_k is None else statistics.pass_k.model_dump(mode="json")
            ),
            "statistical_policy": statistics.model_dump(mode="json"),
        }
    )


def _reveal(
    manifest: HeldoutManifest,
    freeze: CandidateFreeze,
    *,
    task_authorities: tuple[ControlledTaskAuthority, ...] | None = None,
) -> HeldoutRevealReceipt:
    heldout_tasks = tuple(
        task
        for task in (
            _all_task_authorities(manifest) if task_authorities is None else task_authorities
        )
        if task.split_id == "held-out"
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.heldout-reveal-receipt/v1",
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        "candidate_freeze_id": freeze.candidate_freeze_id,
        "candidate_freeze_sha256": freeze.candidate_freeze_sha256,
        "revealed_archive_sha256": manifest.archive_sha256,
        "case_records": [
            {
                "case_id": item.case_id,
                "sealed_plaintext_sha256": item.plaintext_sha256,
                "revealed_authority_sha256": item.plaintext_sha256,
                "task_authority_id": task.authority_id,
                "task_authority_sha256": task.authority_sha256,
            }
            for item, task in zip(manifest.case_commitments, heldout_tasks, strict=True)
        ],
    }
    payload["receipt_id"] = canonical_content_id(payload, excluded=frozenset())
    return HeldoutRevealReceipt.model_validate(payload)


def _freeze_and_final(
    tmp_path: Path,
) -> tuple[
    CandidateFreeze,
    BatchSummaryPackage,
    ControlledRunPlanV2,
    ComparisonPlanV1,
    HeldoutManifest,
]:
    manifest = _manifest()
    plan = _final_plan(manifest)
    pilot = _pilot(tmp_path)
    baseline = next(
        item.prompt_source for item in plan.experiment_specs if item.configuration_id == "baseline"
    )
    candidate = next(
        item.prompt_source for item in plan.experiment_specs if item.configuration_id == "candidate"
    )
    freeze = derive_candidate_freeze(
        pilot_package=pilot,
        baseline_prompt_authority=baseline,
        candidate_prompt_authority=candidate,
        visible_corpus_root=VISIBLE_ROOT,
        heldout_manifest=manifest,
    )
    return freeze, pilot, plan, _comparison(plan, manifest), manifest


def test_candidate_freeze_is_derived_from_real_strict_three_case_pilot(
    tmp_path: Path,
) -> None:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    reveal = _reveal(manifest, freeze)

    receipt = verify_candidate_freeze(
        freeze,
        pilot_package=pilot,
        run_plan=plan,
        comparison_plan=comparison,
        visible_corpus_root=VISIBLE_ROOT,
        heldout_manifest=manifest,
        reveal_receipt=reveal,
        task_authorities=_all_task_authorities(manifest),
    )

    assert freeze.pilot.prohibited_from_final_live_batch is True
    assert freeze.pilot.development_case_ids == tuple(sorted(DEV_NAMES))
    assert freeze.leading_failure.code == "interval_boundary_v1"
    assert freeze.leading_failure.count == 1
    assert len(plan.expand_trial_slots()) == 54
    assert receipt.candidate_freeze_id == freeze.candidate_freeze_id
    assert receipt.run_plan_id == plan.run_plan_id


def test_final_comparison_reverifies_authorities_before_delegate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    reveal = _reveal(manifest, freeze)
    delegated = False

    def forbidden_delegate(*args: object, **kwargs: object) -> object:
        nonlocal delegated
        delegated = True
        raise AssertionError("comparison delegate must remain unreachable")

    monkeypatch.setattr(improvement_loop_module, "assemble_comparison_input", forbidden_delegate)
    forged = freeze.model_copy(update={"visible_corpus_sha256": "0" * 64})

    with pytest.raises(ContractError):
        assemble_final_comparison_input(
            pilot,
            plan,
            comparison,
            freeze=forged,
            pilot_package=pilot,
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
            reveal_receipt=reveal,
            task_authorities=_all_task_authorities(manifest),
        )

    assert delegated is False


@pytest.mark.parametrize(
    "mutation",
    (
        "archive-digest",
        "sealed-digest",
        "revealed-authority-digest",
        "task-authority-id",
        "task-authority-digest",
        "reordered-records",
        "substituted-task",
        "reordered-tasks",
    ),
)
def test_reidentified_reveal_or_substituted_task_cannot_reach_comparison_delegate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    reveal = _reveal(manifest, freeze)
    tasks = list(_all_task_authorities(manifest))
    if mutation == "reordered-tasks":
        heldout = [index for index, task in enumerate(tasks) if task.split_id == "held-out"]
        tasks[heldout[0]], tasks[heldout[1]] = tasks[heldout[1]], tasks[heldout[0]]
    elif mutation == "substituted-task":
        index = next(index for index, task in enumerate(tasks) if task.split_id == "held-out")
        payload = reconstructed_revealed_case(tasks[index]).model_dump(mode="json")
        task_projection = payload["task"]
        assert isinstance(task_projection, dict)
        task_projection["instruction"] = "A canonically different substituted instruction."
        tasks[index] = task_from_revealed_case(HeldoutArchiveCase.model_validate(payload))
    else:
        if mutation == "archive-digest":
            reveal = reveal.model_copy(update={"revealed_archive_sha256": "0" * 64})
        else:
            records = list(reveal.case_records)
            if mutation == "reordered-records":
                records[0], records[1] = records[1], records[0]
            else:
                field = {
                    "sealed-digest": "sealed_plaintext_sha256",
                    "revealed-authority-digest": "revealed_authority_sha256",
                    "task-authority-id": "task_authority_id",
                    "task-authority-digest": "task_authority_sha256",
                }[mutation]
                records[0] = records[0].model_copy(update={field: "0" * 64})
            reveal = reveal.model_copy(update={"case_records": tuple(records)})
        identity = canonical_content_id(
            reveal.model_dump(mode="json", exclude={"receipt_id"}), excluded=frozenset()
        )
        reveal = reveal.model_copy(update={"receipt_id": identity})
    delegated = False

    def forbidden_delegate(*args: object, **kwargs: object) -> object:
        nonlocal delegated
        delegated = True
        raise AssertionError("comparison delegate must remain unreachable")

    monkeypatch.setattr(improvement_loop_module, "assemble_comparison_input", forbidden_delegate)

    with pytest.raises(ContractError):
        assemble_final_comparison_input(
            pilot,
            plan,
            comparison,
            freeze=freeze,
            pilot_package=pilot,
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
            reveal_receipt=reveal,
            task_authorities=tuple(tasks),
        )

    assert delegated is False


def test_coherent_task_plan_and_receipt_forgery_cannot_replace_manifest_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze, pilot, _, _, manifest = _freeze_and_final(tmp_path)
    tasks = list(_all_task_authorities(manifest))
    index = next(index for index, task in enumerate(tasks) if task.split_id == "held-out")
    projection = reconstructed_revealed_case(tasks[index]).model_dump(mode="json")
    task_projection = projection["task"]
    assert isinstance(task_projection, dict)
    task_projection["instruction"] = "A coherently reidentified substituted instruction."
    tasks[index] = task_from_revealed_case(HeldoutArchiveCase.model_validate(projection))
    forged_tasks = tuple(tasks)
    forged_plan = _final_plan(manifest, task_authorities=forged_tasks)
    forged_comparison = _comparison(forged_plan, manifest)
    forged_reveal = _reveal(
        manifest,
        freeze,
        task_authorities=forged_tasks,
    )
    delegated = False

    def forbidden_delegate(*args: object, **kwargs: object) -> object:
        nonlocal delegated
        delegated = True
        raise AssertionError("comparison delegate must remain unreachable")

    monkeypatch.setattr(improvement_loop_module, "assemble_comparison_input", forbidden_delegate)

    with pytest.raises(ContractError, match="exact revealed authorities"):
        assemble_final_comparison_input(
            pilot,
            forged_plan,
            forged_comparison,
            freeze=freeze,
            pilot_package=pilot,
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
            reveal_receipt=forged_reveal,
            task_authorities=forged_tasks,
        )

    assert delegated is False


@pytest.mark.parametrize("drift", ("freeze", "run-plan", "policy", "seal", "reveal"))
def test_final_verifier_rejects_freeze_plan_policy_seal_and_reveal_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    reveal = _reveal(manifest, freeze)
    if drift == "freeze":
        freeze = freeze.model_copy(update={"visible_corpus_sha256": "0" * 64})
    elif drift == "run-plan":
        plan = _final_plan(manifest, candidate_prompt="Apply an unrelated candidate prompt.")
        comparison = _comparison(plan, manifest)
    elif drift == "policy":
        payload = comparison.model_dump(
            mode="json", exclude={"comparison_plan_id", "comparison_plan_sha256"}
        )
        primary = payload["primary_outcome"]
        assert isinstance(primary, dict)
        primary["practical_threshold_basis_points"] = 999
        comparison = materialize_comparison_plan(payload)
    elif drift == "seal":
        manifest = manifest.model_copy(update={"ciphertext_sha256": "0" * 64})
    else:
        reveal = reveal.model_copy(update={"candidate_freeze_sha256": "0" * 64})

    with pytest.raises(ContractError):
        verify_candidate_freeze(
            freeze,
            pilot_package=pilot,
            run_plan=plan,
            comparison_plan=comparison,
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
            reveal_receipt=reveal,
            task_authorities=_all_task_authorities(manifest),
        )


def _canonically_reidentify_freeze(
    freeze: CandidateFreeze,
    *,
    mutation: str,
) -> CandidateFreeze:
    payload = freeze.model_dump(
        mode="json", exclude={"candidate_freeze_id", "candidate_freeze_sha256"}
    )
    pilot = payload["pilot"]
    leading = payload["leading_failure"]
    assert isinstance(pilot, dict)
    assert isinstance(leading, dict)
    if mutation == "leading":
        leading["code"] = "ledger_cents_sign_v1"
        candidate = materialize_authority_source(
            "treatment-prompt",
            {
                "selected_failure": {
                    "code": leading["code"],
                    "profile_id": leading["profile_id"],
                    "profile_version": leading["profile_version"],
                },
                "text": "Inspect the leading failure, then repair the project.",
            },
        )
        payload["candidate_prompt_authority"] = candidate.model_dump(mode="json")
    elif mutation == "development-cases":
        pilot["development_case_ids"] = [
            "dev-integer-ledger",
            "dev-interval-merge",
            "reg-csv-rollup",
        ]
    else:
        pilot["summary_id"] = "batch-summary-canonically-reidentified-tamper"
        pilot["summary_sha256"] = "0" * 64
    pilot["pilot_id"] = canonical_content_id(pilot, excluded=frozenset({"pilot_id"}))
    identity = canonical_content_id(payload, excluded=frozenset())
    payload["candidate_freeze_id"] = f"candidate-freeze-{identity}"
    payload["candidate_freeze_sha256"] = identity
    return CandidateFreeze.model_validate(payload)


@pytest.mark.parametrize("mutation", ("leading", "development-cases", "pilot"))
def test_final_verifier_rejects_canonically_reidentified_pilot_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    forged = _canonically_reidentify_freeze(freeze, mutation=mutation)
    reveal = _reveal(manifest, forged)

    with pytest.raises(ContractError, match="exact derivation|development split"):
        verify_candidate_freeze(
            forged,
            pilot_package=pilot,
            run_plan=plan,
            comparison_plan=comparison,
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
            reveal_receipt=reveal,
            task_authorities=_all_task_authorities(manifest),
        )


def test_no_development_failure_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest()
    plan = _final_plan(manifest)
    with pytest.raises(ContractError, match="no usable"):
        derive_candidate_freeze(
            pilot_package=_pilot(tmp_path, pass_all=True),
            baseline_prompt_authority=next(
                item.prompt_source
                for item in plan.experiment_specs
                if item.configuration_id == "baseline"
            ),
            candidate_prompt_authority=next(
                item.prompt_source
                for item in plan.experiment_specs
                if item.configuration_id == "candidate"
            ),
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
        )


def test_final_verifier_rejects_canonically_reidentified_regression_split(
    tmp_path: Path,
) -> None:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    reveal = _reveal(manifest, freeze)
    tasks = list(_all_task_authorities(manifest))
    index = next(index for index, task in enumerate(tasks) if task.split_id == "regression")
    task = tasks[index]
    payload = task.model_dump(mode="json", exclude={"authority_id"})
    payload["split_id"] = "development"
    tasks[index] = materialize_controlled_task(payload)

    with pytest.raises(ContractError, match="task split"):
        verify_candidate_freeze(
            freeze,
            pilot_package=pilot,
            run_plan=plan,
            comparison_plan=comparison,
            visible_corpus_root=VISIBLE_ROOT,
            heldout_manifest=manifest,
            reveal_receipt=reveal,
            task_authorities=tuple(tasks),
        )


def test_visible_corpus_digest_is_content_identified(tmp_path: Path) -> None:
    source = tmp_path / "visible"
    shutil.copytree(VISIBLE_ROOT, source)
    first = visible_corpus_digest(source)
    (source / "dev-interval-merge" / "solution.py").write_text(
        "def merge(value): return value\n", encoding="utf-8"
    )

    assert visible_corpus_digest(source) != first
