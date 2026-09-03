from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.freeze import AttemptCapture, RequestedState, freeze_attempt
from cernora_reference_workflow.offline import evaluate_frozen_export
from cernora_reference_workflow.report_builder import build_run_report
from cernora_reference_workflow.runtime_policy import OperatorInterruptReceipt
from cernora_reference_workflow.spec_builder import build_tiny_calculator_spec
from cernora_reference_workflow.test_runner import ResourceReceipt

ROOT = Path(__file__).resolve().parents[2]
TASK = ROOT / "tasks/tiny-calculator-v1"


@pytest.mark.parametrize("state", ("timed-out", "interrupted"))
def test_noncompleted_lifecycle_remains_inconclusive_with_passing_tests(
    tmp_path: Path, state: RequestedState
) -> None:
    candidate = tmp_path / "candidate/src"
    candidate.mkdir(parents=True)
    (candidate.parent / "pyproject.toml").write_bytes(
        (TASK / "environment/pyproject.toml").read_bytes()
    )
    (candidate / "calc.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(TASK / "tests/run_tests.py"),
            "--candidate-root",
            str(candidate.parent),
        ],
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0
    stdout = tmp_path / "stdout.txt"
    stderr = tmp_path / "stderr.txt"
    stdout.write_bytes(completed.stdout)
    stderr.write_bytes(completed.stderr)
    exports = tmp_path / "exports"
    exports.mkdir()
    export = exports / state
    runtime_files: tuple[tuple[str, Path], ...] = ()
    if state == "interrupted":
        interruption = tmp_path / "operator-interrupt.json"
        interruption.write_bytes(
            canonical_json_bytes(
                OperatorInterruptReceipt(
                    schema_version="cernora.reference.operator-interrupt/v1",
                    operator_signal="SIGINT",
                    target="active-pi-process",
                    verified_signal_count=1,
                ).model_dump(mode="json")
            )
        )
        runtime_files = (("runtime/operator-interrupt.json", interruption),)
    terminal = freeze_attempt(
        spec=build_tiny_calculator_spec(ROOT),
        capture=AttemptCapture(
            source_trial_id=f"synthetic-{state}-conformance-only",
            candidate_root=candidate.parent,
            test_stdout=stdout,
            test_stderr=stderr,
            test_exit_code=0,
            resource_receipt=ResourceReceipt(
                schema_version="cernora.reference.resource-receipt/v1",
                duration_milliseconds=1,
                peak_memory_bytes=None,
                cpu_milliseconds=None,
            ),
            runtime_files=runtime_files,
        ),
        baseline_root=TASK / "environment",
        test_plan_path=TASK / "tests/test-plan.json",
        requested_state=state,
        predecessor_attempt_id=None,
        destination=export,
    )
    assert terminal.state == state
    assert terminal.retry_eligible is False
    evaluation = evaluate_frozen_export(
        spec=build_tiny_calculator_spec(ROOT),
        export_root=export,
        output_root=tmp_path / "offline",
    )
    assert evaluation.receipt.case_outcome == "inconclusive"
    report = build_run_report(
        spec=build_tiny_calculator_spec(ROOT),
        export_root=export,
        evaluation=evaluation,
        portable_spec_path="examples/tiny-calculator-v1.json",
        portable_export_path=f"exports/{state}",
        portable_bundle_path=f"reports/{state}/adapted/bundle.json",
        portable_evaluation_path=f"reports/{state}/evaluated",
    )
    assert report.evaluation.validity == "invalid"
    assert report.evaluation.behavioral_decision == "inconclusive"
    assert report.evaluation.strict_reload.status == "verified"
