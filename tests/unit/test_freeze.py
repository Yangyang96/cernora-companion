from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path

from cernora_reference_workflow.common import (
    canonical_json_bytes,
    load_json_file,
    sha256_bytes,
    sha256_file,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec, materialize_experiment_spec
from cernora_reference_workflow.export import verify_completed_export
from cernora_reference_workflow.freeze import AttemptCapture, freeze_attempt
from cernora_reference_workflow.profile import create_profile
from cernora_reference_workflow.runtime_agent import RUNTIME_CONFIGURATION_SHA256
from cernora_reference_workflow.test_runner import (
    TEST_IDS,
    RawTestResults,
    ResourceReceipt,
    canonical_raw_test_output,
)
from cernora_reference_workflow.test_runner import (
    TestPlan as FrozenTestPlan,
)

from .test_experiment_spec import valid_payload

ROOT = Path(__file__).resolve().parents[2]
TASK = ROOT / "tasks/tiny-calculator-v1"


def _spec() -> ExperimentSpec:
    payload = deepcopy(valid_payload())
    plan_path = TASK / "tests/test-plan.json"
    plan_payload = load_json_file(plan_path)
    assert isinstance(plan_payload, dict)
    plan = FrozenTestPlan.model_validate(plan_payload)
    test_runner = payload["test_runner"]
    assert isinstance(test_runner, dict)
    test_runner.update(
        {
            "authority_sha256": plan.authority_sha256,
            "test_plan_sha256": sha256_file(plan_path),
            "test_source_sha256": plan.test_source_sha256,
            "command": list(plan.command),
            "working_directory": "candidate",
        }
    )
    runtime = payload["runtime"]
    assert isinstance(runtime, dict)
    runtime["configuration_sha256"] = RUNTIME_CONFIGURATION_SHA256
    profile = payload["profile"]
    assert isinstance(profile, dict)
    authority = create_profile().authority
    profile["authority_sha256"] = sha256_bytes(
        canonical_json_bytes(authority.model_dump(mode="json", exclude_none=False))
    )
    return materialize_experiment_spec(payload)


def _capture(tmp_path: Path, *, add_unapproved_file: bool = False) -> AttemptCapture:
    candidate = tmp_path / "candidate"
    (candidate / "src").mkdir(parents=True)
    shutil.copyfile(TASK / "environment/pyproject.toml", candidate / "pyproject.toml")
    (candidate / "src/calc.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    if add_unapproved_file:
        (candidate / "README.md").write_text("unapproved\n", encoding="utf-8")
    expected = (-1, -5, 5, 4)
    categories = ("fail-to-pass", "fail-to-pass", "pass-to-pass", "pass-to-pass")
    raw = RawTestResults.model_validate(
        {
            "schema_version": "cernora.reference.test-results/v1",
            "termination": "exited",
            "tests": [
                {
                    "test_id": test_id,
                    "category": categories[index],
                    "passed": True,
                    "expected": expected[index],
                    "actual": expected[index],
                    "error": None,
                }
                for index, test_id in enumerate(TEST_IDS)
            ],
            "runner_error": None,
        }
    )
    stdout = tmp_path / "stdout.txt"
    stderr = tmp_path / "stderr.txt"
    stdout.write_bytes(canonical_raw_test_output(raw))
    stderr.write_bytes(b"")
    return AttemptCapture(
        source_trial_id="fixture-trial",
        candidate_root=candidate,
        test_stdout=stdout,
        test_stderr=stderr,
        test_exit_code=0,
        resource_receipt=ResourceReceipt(
            schema_version="cernora.reference.resource-receipt/v1",
            duration_milliseconds=None,
            peak_memory_bytes=None,
            cpu_milliseconds=None,
        ),
    )


def test_freeze_publishes_verified_passing_attempt(tmp_path: Path) -> None:
    destination = tmp_path / "completed"
    terminal = freeze_attempt(
        spec=_spec(),
        capture=_capture(tmp_path),
        baseline_root=TASK / "environment",
        test_plan_path=TASK / "tests/test-plan.json",
        requested_state="completed",
        predecessor_attempt_id=None,
        destination=destination,
    )
    manifest = verify_completed_export(destination)
    assert terminal.state == "completed"
    assert manifest.lifecycle_outcome == "completed"


def test_freeze_classifies_unapproved_candidate_change_as_behavioral_failure(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "completed"
    terminal = freeze_attempt(
        spec=_spec(),
        capture=_capture(tmp_path, add_unapproved_file=True),
        baseline_root=TASK / "environment",
        test_plan_path=TASK / "tests/test-plan.json",
        requested_state="completed",
        predecessor_attempt_id=None,
        destination=destination,
    )
    manifest = verify_completed_export(destination)
    assert terminal.state == "behavioral-failure"
    assert manifest.lifecycle_outcome == "behavioral-failure"
