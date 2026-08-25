from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cernora_reference_workflow.test_runner import (
    TEST_IDS,
)
from cernora_reference_workflow.test_runner import (
    TestPlan as RunnerTestPlan,
)
from cernora_reference_workflow.test_runner import (
    TestResults as RunnerTestResults,
)

ROOT = Path(__file__).resolve().parents[2]
TASK = ROOT / "tasks/tiny-calculator-v1"
TASK_V2 = ROOT / "tasks/tiny-calculator-v2"


def invoke(candidate: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(TASK / "tests/run_tests.py"),
            "--candidate-root",
            str(candidate),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def invoke_v2(candidate: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(TASK_V2 / "tests/run_tests.py"),
            "--candidate-root",
            str(candidate),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_original_candidate_proves_two_real_failures_and_two_regressions() -> None:
    result = invoke(TASK / "environment")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert [item["test_id"] for item in payload["tests"]] == list(TEST_IDS)
    assert [item["passed"] for item in payload["tests"]] == [False, False, True, True]


def test_v2_original_candidate_proves_nine_failures_and_three_regressions() -> None:
    result = invoke_v2(TASK_V2 / "environment")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert len(payload["tests"]) == 12
    assert [item["passed"] for item in payload["tests"]] == [False] * 9 + [True] * 3
    plan = RunnerTestPlan.model_validate_json((TASK_V2 / "tests/test-plan.json").read_bytes())
    assert [item["test_id"] for item in payload["tests"]] == list(plan.test_ids)


def test_repaired_candidate_passes_all_authority_cases(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate/src"
    candidate.mkdir(parents=True)
    (candidate / "calc.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    result = invoke(candidate.parent)
    assert result.returncode == 0
    assert all(item["passed"] for item in json.loads(result.stdout)["tests"])


def test_plan_and_results_enforce_complete_authority() -> None:
    plan = RunnerTestPlan.model_validate_json((TASK / "tests/test-plan.json").read_bytes())
    payload = {
        "schema_version": "cernora.reference.test-results/v1",
        "test_plan_sha256": "0" * 64,
        "test_source_sha256": "1" * 64,
        "termination": "exited",
        "exit_code": 0,
        "tests": [
            {
                "test_id": test_id,
                "category": "fail-to-pass" if index < 2 else "pass-to-pass",
                "passed": True,
                "expected": index,
                "actual": index,
                "error": None,
            }
            for index, test_id in enumerate(TEST_IDS)
        ],
        "runner_error": None,
        "pre_candidate_tree_sha256": "2" * 64,
        "post_candidate_tree_sha256": "3" * 64,
        "changed_paths": ["src/calc.py"],
        "protected_paths_unchanged": True,
    }
    assert plan.test_ids == TEST_IDS
    assert RunnerTestResults.model_validate(payload).verdict == "pass"
