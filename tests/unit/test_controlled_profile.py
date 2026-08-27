from __future__ import annotations

import hashlib
from pathlib import Path

from cernora import read_evaluation_report

from cernora_reference_workflow.controlled_evaluation import materialize_repair_result
from cernora_reference_workflow.controlled_profile import (
    ControlledRepairProfile,
    evaluate_repair_result_package,
)
from cernora_reference_workflow.controlled_task import load_visible_task


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_real_profile_pipeline_publishes_strict_core_evaluation_receipt(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    result = materialize_repair_result(
        {
            "schema_version": "cernora.reference.repair-result/v1",
            "case_id": task.case.case_id,
            "result_record_version": "agent.evaluator.result-record/v1",
            "test_authority_sha256": digest("test-authority"),
            "test_plan_sha256": digest("test-plan"),
            "test_source_sha256": task.test_source_sha256,
            "termination": "exited",
            "exit_code": 0,
            "checks": [
                {
                    "check_id": "frozen-verifier",
                    "failure_code": task.failure_code,
                    "passed": True,
                }
            ],
            "allowed_paths": list(task.allowed_paths),
            "changed_paths": list(task.allowed_paths),
            "protected_paths": list(task.protected_paths),
            "protected_path_receipt": {
                "before_sha256": digest("protected"),
                "after_sha256": digest("protected"),
                "unchanged": True,
            },
        }
    )
    output = tmp_path / "evaluation"

    package = evaluate_repair_result_package(
        task=task,
        tasks=(task,),
        result=result,
        source_attempt_id=digest("attempt"),
        output=output,
    )

    profile = ControlledRepairProfile((task,))
    report = read_evaluation_report(output / "evaluated", profile)
    assert report is not None
    assert report.conclusion == "pass"
    assert report.evaluation_validity == "valid"
    assert {item.id for item in report.records if item.role in {"outcome", "constraint"}} == {
        "authorized_paths_only_v1",
        "protected_paths_unchanged_v1",
        "repair_success_v1",
    }
    assert package.file_payloads()["evaluation-report.json"]
