from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from cernora_reference_workflow.freeze import AttemptCapture, freeze_attempt
from cernora_reference_workflow.offline import evaluate_frozen_export
from cernora_reference_workflow.report import publish_run_report
from cernora_reference_workflow.report_builder import build_run_report
from cernora_reference_workflow.spec_builder import build_tiny_calculator_v2_spec
from cernora_reference_workflow.test_runner import ResourceReceipt

ROOT = Path(__file__).resolve().parents[2]
TASK = ROOT / "tasks/tiny-calculator-v2"


def test_v2_complete_failing_receipt_evaluates_as_valid_fail(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    shutil.copyfile(TASK / "environment/pyproject.toml", candidate / "pyproject.toml")
    shutil.copytree(TASK / "environment/src", candidate / "src")
    completed = subprocess.run(
        [
            sys.executable,
            str(TASK / "tests/run_tests.py"),
            "--candidate-root",
            str(candidate),
        ],
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 1
    stdout = tmp_path / "stdout.txt"
    stderr = tmp_path / "stderr.txt"
    stdout.write_bytes(completed.stdout)
    stderr.write_bytes(completed.stderr)
    export_parent = tmp_path / "exports"
    export_parent.mkdir()
    export = export_parent / "behavioral-failure"
    terminal = freeze_attempt(
        spec=build_tiny_calculator_v2_spec(ROOT),
        capture=AttemptCapture(
            source_trial_id="synthetic-v2-conformance-only",
            candidate_root=candidate,
            test_stdout=stdout,
            test_stderr=stderr,
            test_exit_code=completed.returncode,
            resource_receipt=ResourceReceipt(
                schema_version="cernora.reference.resource-receipt/v1",
                duration_milliseconds=1,
                peak_memory_bytes=None,
                cpu_milliseconds=None,
            ),
        ),
        baseline_root=TASK / "environment",
        test_plan_path=TASK / "tests/test-plan.json",
        requested_state="completed",
        predecessor_attempt_id=None,
        destination=export,
    )
    assert terminal.state == "behavioral-failure"
    evaluation = evaluate_frozen_export(
        spec=build_tiny_calculator_v2_spec(ROOT),
        export_root=export,
        output_root=tmp_path / "offline",
    )
    assert evaluation.receipt.case_outcome == "fail"
    report = build_run_report(
        spec=build_tiny_calculator_v2_spec(ROOT),
        export_root=export,
        evaluation=evaluation,
        portable_spec_path="examples/tiny-calculator-v2.json",
        portable_export_path="exports/behavioral-failure",
        portable_bundle_path="reports/behavioral-failure/adapted/bundle.json",
        portable_evaluation_path="reports/behavioral-failure/evaluated",
    )
    assert report.components.task.task_id == "tiny-calculator-v2"
    assert report.evaluation.validity == "valid"
    assert report.evaluation.behavioral_decision == "fail"
    reports = tmp_path / "reports"
    reports.mkdir()
    publish_run_report(report, reports / "behavioral-failure")
    assert (reports / "behavioral-failure/run-report.json").read_bytes() == report.canonical_bytes()
