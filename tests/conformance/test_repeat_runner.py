from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import pytest
from cernora import reload_batch_summary
from pydantic import ValidationError

from cernora_reference_workflow.attempt_record import publish_preterminal_attempt
from cernora_reference_workflow.batch_summary import (
    normalize_execution_pack,
    summarize_execution_pack,
)
from cernora_reference_workflow.cli import main
from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
)
from cernora_reference_workflow.execution import verify_execution_pack
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.freeze import AttemptCapture, freeze_attempt
from cernora_reference_workflow.lifecycle import materialize_preterminal_record
from cernora_reference_workflow.run_plan import RunPlan, materialize_run_plan
from cernora_reference_workflow.runner import resume_repeat, run_repeat
from cernora_reference_workflow.test_runner import (
    RawTestResults,
    ResourceReceipt,
    canonical_raw_test_output,
)
from cernora_reference_workflow.test_runner import TestCaseResult as FrozenTestCaseResult
from cernora_reference_workflow.test_runner import TestPlan as FrozenTestPlan

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_VALUES = {
    "tiny-calculator-v1": (-1, -5, 5, 4),
    "tiny-calculator-v2": (14, 20, 3, -2, 4, 1, 1, 1, 1, 7, 5, -4),
}


def _plan_payload() -> dict[str, object]:
    specs = tuple(
        ExperimentSpec.from_file(ROOT / f"examples/tiny-calculator-{version}.json")
        for version in ("v1", "v2")
    )
    cells = [
        {
            "case_id": spec.task.task_id,
            "configuration_id": configuration,
            "experiment_id": spec.experiment_id,
        }
        for spec in specs
        for configuration in ("baseline", "candidate")
    ]
    return {
        "schema_version": "cernora.reference.run-plan/v1",
        "companion_version": "0.2.0",
        "cernora_version": "0.1.2",
        "connector": {
            "connector_id": "cernora-reference-harbor-pi",
            "connector_version": "2",
            "platform_qualification": "macos-arm64",
        },
        "experiment_specs": [item.model_dump(mode="json") for item in specs],
        "cases": [
            {
                "case_id": item.task.task_id,
                "case_version": item.task.task_version,
                "task_content_sha256": item.task.content_sha256,
            }
            for item in specs
        ],
        "configurations": [
            {"configuration_id": "baseline"},
            {"configuration_id": "candidate"},
        ],
        "cells": cells,
        "repetitions": 3,
        "pairing_rule": "case-configuration-repetition",
        "planned_trial_count": 12,
        "worst_case_attempt_count": 24,
        "execution": {
            "concurrency": 1,
            "max_attempt_count": 24,
            "max_total_wall_time_seconds": 86400,
            "token_budget": {
                "status": "unavailable",
                "reason": "no-structured-authoritative-source",
            },
            "monetary_budget": {
                "status": "unavailable",
                "reason": "no-structured-authoritative-source",
            },
        },
        "analysis": {
            "method": "none",
            "method_version": "m1",
            "aggregate_quality_conclusion": False,
        },
    }


def _single_trial_plan(*, max_attempt_count: int = 2) -> RunPlan:
    payload = _plan_payload()
    assert isinstance(payload["experiment_specs"], list)
    assert isinstance(payload["cases"], list)
    assert isinstance(payload["configurations"], list)
    assert isinstance(payload["cells"], list)
    assert isinstance(payload["execution"], dict)
    payload["experiment_specs"] = payload["experiment_specs"][:1]
    payload["cases"] = payload["cases"][:1]
    payload["configurations"] = payload["configurations"][:1]
    payload["cells"] = payload["cells"][:1]
    payload["repetitions"] = 1
    payload["planned_trial_count"] = 1
    payload["worst_case_attempt_count"] = 2
    payload["execution"]["max_attempt_count"] = max_attempt_count
    return materialize_run_plan(payload)


def _candidate(root: Path, case_id: str, *, successful: bool) -> Path:
    task = ROOT / "tasks" / case_id / "environment"
    candidate = root / "candidate"
    (candidate / "src").mkdir(parents=True)
    shutil.copyfile(task / "pyproject.toml", candidate / "pyproject.toml")
    if successful and case_id == "tiny-calculator-v1":
        (candidate / "src/calc.py").write_text(
            '"""Deterministic conformance candidate."""\n\n\n'
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n",
            encoding="utf-8",
        )
    else:
        shutil.copyfile(task / "src/calc.py", candidate / "src/calc.py")
    return candidate


def _raw_receipt(case_id: str, *, passing: bool) -> RawTestResults:
    payload = load_json_file(ROOT / "tasks" / case_id / "tests/test-plan.json")
    assert isinstance(payload, dict)
    test_plan = FrozenTestPlan.model_validate(payload)
    tests = tuple(
        FrozenTestCaseResult(
            test_id=test_id,
            category="fail-to-pass" if test_id.startswith("fail_to_pass") else "pass-to-pass",
            passed=passing or index != 0,
            expected=EXPECTED_VALUES[case_id][index],
            actual=EXPECTED_VALUES[case_id][index] if passing or index != 0 else None,
            error=None if passing or index != 0 else "deterministic-fixture-failure",
        )
        for index, test_id in enumerate(test_plan.test_ids)
    )
    return RawTestResults(
        schema_version="cernora.reference.test-results/v1",
        termination="exited",
        tests=tests,
        runner_error=None,
    )


def _publish_completed(request: Any, *, lifecycle: str) -> None:
    specification = request.specification
    destination = request.destination
    active = request.active_record
    slot = request.trial.slot
    fixture = destination.parent / f".fixture-{active.ordinal}"
    fixture.mkdir()
    stdout = fixture / "stdout.txt"
    stderr = fixture / "stderr.txt"
    stderr.write_text("", encoding="utf-8")
    passing = lifecycle == "completed"
    requested_state: Literal["timed-out", "completed"] = (
        "timed-out" if lifecycle == "timed-out" else "completed"
    )
    if requested_state == "timed-out":
        stdout.write_bytes(b"")
        exit_code = None
    else:
        stdout.write_bytes(canonical_raw_test_output(_raw_receipt(slot.case_id, passing=passing)))
        exit_code = 0 if passing else 1
    candidate = _candidate(fixture, slot.case_id, successful=passing)
    freeze_attempt(
        spec=specification,
        capture=AttemptCapture(
            source_trial_id=f"fixture-{slot.slot_index}-{active.ordinal}",
            candidate_root=candidate,
            test_stdout=stdout,
            test_stderr=stderr,
            test_exit_code=exit_code,
            resource_receipt=ResourceReceipt(
                schema_version="cernora.reference.resource-receipt/v1",
                duration_milliseconds=1,
                peak_memory_bytes=None,
                cpu_milliseconds=None,
            ),
        ),
        baseline_root=ROOT / "tasks" / slot.case_id / "environment",
        test_plan_path=ROOT / "tasks" / slot.case_id / "tests/test-plan.json",
        requested_state=requested_state,
        predecessor_attempt_id=active.predecessor_attempt_id,
        destination=destination,
    )
    shutil.rmtree(fixture)


def _publish_preterminal(request: Any, state: str) -> None:
    specification = request.specification
    destination = request.destination
    active = request.active_record
    slot = request.trial.slot
    source_trial_id = f"fixture-{slot.slot_index}-{active.ordinal}"
    terminal = materialize_preterminal_record(
        experiment_id=specification.experiment_id,
        source_trial_id=source_trial_id,
        state=state,  # type: ignore[arg-type]
        predecessor_attempt_id=active.predecessor_attempt_id,
    )
    publish_preterminal_attempt(
        destination=destination,
        experiment_id=specification.experiment_id,
        terminal=terminal,
        source_trial_id=source_trial_id,
    )


class _LifecycleMatrixExecutor:
    def __init__(self) -> None:
        self.slot_order: list[int] = []

    def __call__(self, request: Any) -> None:
        slot_index = request.trial.slot.slot_index
        ordinal = request.active_record.ordinal
        self.slot_order.append(slot_index)
        fixture_kind = slot_index % 4
        if fixture_kind == 1:
            _publish_completed(request, lifecycle="completed")
        elif fixture_kind == 2:
            _publish_completed(request, lifecycle="behavioral-failure")
        elif fixture_kind == 3:
            _publish_completed(request, lifecycle="timed-out")
        else:
            retry_state = (
                "transient-provider-pre-terminal"
                if ordinal == 1
                else "runtime-pre-terminal-failure"
            )
            _publish_preterminal(
                request,
                retry_state,
            )


def test_public_repeat_runner_closes_ordered_lifecycle_matrix_and_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = materialize_run_plan(_plan_payload())
    executor = _LifecycleMatrixExecutor()
    outcome = run_repeat(
        tmp_path / "execution",
        plan,
        executor,
        nonce="1" * 64,
        sleeper=lambda _seconds: None,
    )

    assert outcome.status == "completed"
    assert len(outcome.state.trial_manifests) == 12
    assert len(outcome.state.active_attempts) == 15
    assert executor.slot_order == [1, 2, 3, 4, 4, 5, 6, 7, 8, 8, 9, 10, 11, 12, 12]
    assert [item.slot.repetition for item in outcome.state.trial_slots.slots] == [1, 2, 3] * 4
    assert [item.terminal_state for item in outcome.state.trial_manifests] == [
        "completed",
        "behavioral-failure",
        "timed-out",
        "runtime-pre-terminal-failure",
    ] * 3
    assert [item.evaluation.status for item in outcome.state.trial_results] == [
        "available",
        "available",
        "available",
        "unavailable",
    ] * 3
    assert outcome.pack_root is not None
    verify_execution_pack(outcome.pack_root)

    batch_input = normalize_execution_pack(outcome.pack_root)
    assert batch_input.planned_trial_count == 12
    assert batch_input.attempt_count == 15
    assert all(
        attempt.attempt_id != attempt.source_attempt_id
        for trial in batch_input.trials
        for attempt in trial.attempts
    )
    retry_trials = tuple(trial for trial in batch_input.trials if len(trial.attempts) == 2)
    assert len(retry_trials) == 3
    assert all(
        trial.attempts[1].predecessor_attempt_id == trial.attempts[0].attempt_id
        for trial in retry_trials
    )

    summary_trees: list[dict[str, bytes]] = []
    summaries = []
    for index in range(3):
        summary_root = tmp_path / f"summary-{index}"
        summaries.append(summarize_execution_pack(outcome.pack_root, summary_root))
        summary_trees.append(
            {name: path.read_bytes() for name, path in closed_regular_tree(summary_root).items()}
        )
    assert summaries[0] == summaries[1] == summaries[2]
    assert summary_trees[0] == summary_trees[1] == summary_trees[2]
    assert summaries[0].overall.outcomes.model_dump() == {
        "passed": 3,
        "behavioral_failed": 3,
        "evaluation_invalid": 3,
        "infrastructure_unavailable": 3,
    }
    assert summaries[0].attempt_diagnostics.retry_attempts == 3
    assert reload_batch_summary(tmp_path / "summary-0") == summaries[0]
    summary_payload = canonical_json_bytes(summaries[0].model_dump(mode="json"))
    markdown = (tmp_path / "summary-0/batch-summary.md").read_bytes()
    for forbidden in (b"winner", b"delta", b"confidence", b"pass@k", b"improvement"):
        assert forbidden not in summary_payload.lower()
        assert forbidden not in markdown.lower()

    cli_output = tmp_path / "cli-summary"
    assert main(["summarize", str(outcome.pack_root), "--output", str(cli_output)]) == 0
    assert '"command":"summarize"' in capsys.readouterr().out
    assert main(["summarize", str(outcome.pack_root), "--output", str(cli_output)]) == 2
    assert "output must be new" in capsys.readouterr().err

    indexed_file = next(
        path
        for path in outcome.pack_root.rglob("*.json")
        if path.relative_to(outcome.pack_root).as_posix() != "manifest.json"
    )
    indexed_file.write_bytes(indexed_file.read_bytes() + b"\n")
    with pytest.raises(ContractError, match="digest|length"):
        verify_execution_pack(outcome.pack_root)
    assert (
        main(["summarize", str(outcome.pack_root), "--output", str(tmp_path / "corrupt-summary")])
        == 3
    )
    assert "failed" in capsys.readouterr().err


@pytest.mark.parametrize("mutation", ("missing", "duplicate"))
def test_public_run_plan_rejects_missing_or_duplicate_trials(mutation: str) -> None:
    payload = deepcopy(_plan_payload())
    assert isinstance(payload["cells"], list)
    assert isinstance(payload["execution"], dict)
    if mutation == "missing":
        payload["cells"] = payload["cells"][:2]
        payload["planned_trial_count"] = 6
        payload["worst_case_attempt_count"] = 12
        payload["execution"]["max_attempt_count"] = 12
    else:
        payload["cells"][1] = deepcopy(payload["cells"][0])
    with pytest.raises((ContractError, ValidationError, ValueError)):
        materialize_run_plan(payload)


def test_public_resume_adopts_terminal_artifact_after_parent_crash(tmp_path: Path) -> None:
    root = tmp_path / "execution"

    def publish_then_crash(request: Any) -> None:
        _publish_preterminal(request, "runtime-pre-terminal-failure")
        raise RuntimeError("simulated-parent-crash")

    with pytest.raises(RuntimeError, match="parent-crash"):
        run_repeat(root, _single_trial_plan(), publish_then_crash, nonce="2" * 64)

    def reject_rerun(request: Any) -> None:
        raise AssertionError(f"adopted Attempt reran: {request!r}")

    outcome = resume_repeat(root, reject_rerun)
    assert outcome.status == "completed"
    assert len(outcome.state.active_attempts) == 1


def test_public_hard_attempt_budget_stops_retry_and_is_not_resumable(tmp_path: Path) -> None:
    root = tmp_path / "execution"

    def eligible(request: Any) -> None:
        _publish_preterminal(request, "transient-provider-pre-terminal")

    outcome = run_repeat(root, _single_trial_plan(max_attempt_count=1), eligible, nonce="3" * 64)
    assert outcome.status == "budget-exhausted"
    assert outcome.state.checkpoints[-1].attempt_count == 1
    assert outcome.state.trial_manifests == ()
    with pytest.raises(ContractError, match="cannot be resumed"):
        resume_repeat(root, eligible)
