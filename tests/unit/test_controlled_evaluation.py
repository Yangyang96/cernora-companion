from __future__ import annotations

from copy import deepcopy

import pytest
from cernora import EvidenceReference
from pydantic import ValidationError

from cernora_reference_workflow.controlled_evaluation import (
    ProtectedPathReceipt,
    RepairResultRecord,
    materialize_repair_result,
)

DIGEST = "1" * 64


def valid_result_payload() -> dict[str, object]:
    return {
        "schema_version": "cernora.reference.repair-result/v1",
        "case_id": "dev-interval-merge",
        "result_record_version": "agent.evaluator.result-record/v1",
        "test_authority_sha256": "2" * 64,
        "test_plan_sha256": "3" * 64,
        "test_source_sha256": "4" * 64,
        "termination": "exited",
        "exit_code": 1,
        "checks": [
            {
                "check_id": "boundary",
                "failure_code": "interval_boundary_v1",
                "passed": False,
            },
            {
                "check_id": "ordinary",
                "failure_code": "interval_ordinary_v1",
                "passed": True,
            },
        ],
        "allowed_paths": ["src/intervals.py"],
        "changed_paths": ["src/intervals.py"],
        "protected_paths": ["tests"],
        "protected_path_receipt": {
            "before_sha256": DIGEST,
            "after_sha256": DIGEST,
            "unchanged": True,
        },
    }


def test_repair_result_is_canonical_and_exposes_versioned_failure_codes() -> None:
    result = materialize_repair_result(valid_result_payload())
    reference = EvidenceReference(
        evidence_id="evidence-1", locator="artifacts/result.json", sha256="5" * 64
    )

    assert not result.passed
    assert result.failure_codes == ("interval_boundary_v1",)
    records = result.core_result_records(reference)
    assert tuple(item.id for item in records) == (
        "authorized_paths_only_v1",
        "diagnostic.interval_boundary_v1",
        "diagnostic.interval_ordinary_v1",
        "protected_paths_unchanged_v1",
        "repair_success_v1",
    )
    assert result == materialize_repair_result(deepcopy(valid_result_payload()))


def test_non_exited_result_is_unavailable_not_behavioral_failure() -> None:
    payload = valid_result_payload()
    payload["termination"] = "timed_out"
    payload["exit_code"] = None
    result = materialize_repair_result(payload)
    reference = EvidenceReference(
        evidence_id="evidence-1", locator="artifacts/result.json", sha256="5" * 64
    )

    record = result.core_result_records(reference)[0]
    assert record.validity == "unavailable"
    assert record.value is None
    assert result.failure_codes == ("evaluation_unavailable_v1",)


@pytest.mark.parametrize("mutation", ("receipt", "path", "duplicate-code", "identity"))
def test_repair_result_rejects_contradiction_and_tamper(mutation: str) -> None:
    payload = valid_result_payload()
    if mutation == "receipt":
        payload["protected_path_receipt"] = ProtectedPathReceipt.model_construct(
            before_sha256="1" * 64, after_sha256="2" * 64, unchanged=True
        ).model_dump(mode="json")
    elif mutation == "path":
        payload["changed_paths"] = ["../escape.py"]
    elif mutation == "duplicate-code":
        checks = payload["checks"]
        assert isinstance(checks, list)
        checks[1]["failure_code"] = checks[0]["failure_code"]
    else:
        payload["result_id"] = "0" * 64
    with pytest.raises((ValidationError, ValueError)):
        if mutation == "identity":
            RepairResultRecord.model_validate(payload)
        else:
            materialize_repair_result(payload)
