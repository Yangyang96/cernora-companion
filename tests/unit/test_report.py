from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.report import (
    ReportError,
    RunReport,
    StrictReloadVerified,
    materialize_run_report,
    publish_run_report,
)

DIGEST = "a" * 64
ATTEMPT = "b" * 64


def available_digest(value: str = DIGEST) -> dict[str, object]:
    return {"status": "available", "sha256": value}


def missing(reason: str = "not-emitted") -> dict[str, object]:
    return {"status": "missing", "reason": reason}


def verified_reload() -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "verified",
        "bundle_id": "reference-bundle",
        "evidence_bundle_sha256": DIGEST,
        "evaluation_input_sha256": DIGEST,
        "evaluation_id": "evaluation-1",
        "evidence_id": "evidence-1",
        "score_id": "score-1",
        "decision_id": "decision-1",
        "gate_decision_sha256": DIGEST,
        "evaluation_receipt_sha256": DIGEST,
    }
    payload["result_identity_sha256"] = StrictReloadVerified.compute_identity(payload)
    return payload


def valid_payload() -> dict[str, object]:
    return {
        "schema_version": "cernora.reference.run-report/v1",
        "experiment_id": DIGEST,
        "components": {
            "task": {
                "task_id": "tiny-calculator-v1",
                "task_version": "1",
                "content_sha256": DIGEST,
                "prompt_sha256": DIGEST,
                "instruction_sha256": DIGEST,
            },
            "container": {
                "image": f"python:3.12.13-slim-bookworm@sha256:{DIGEST}",
                "build_base_image": f"python:3.12.13-slim-bookworm@sha256:{DIGEST}",
                "platform": "linux/arm64",
            },
            "harness": {
                "name": "harbor",
                "version": "0.16.1",
                "configuration_sha256": DIGEST,
            },
            "runtime": {
                "name": "codex",
                "version": "0.148.0",
                "configuration_sha256": DIGEST,
            },
            "model": {
                "name": "gpt-5.6-terra",
                "reasoning_effort": "medium",
                "web_search": False,
                "provider_egress": "required-allowed",
            },
            "cernora": {"package_version": "0.1.2", "wheel_sha256": DIGEST},
            "profile": {
                "profile_id": "cernora-reference-coding-v1",
                "profile_version": "1.0.0",
                "authority_sha256": DIGEST,
            },
            "adapter": {
                "adapter_id": "cernora-reference-adapter",
                "adapter_version": "1",
                "authority_sha256": DIGEST,
            },
        },
        "attempts": [
            {
                "ordinal": 1,
                "attempt_id": ATTEMPT,
                "predecessor_attempt_id": None,
                "retry_delay_seconds": None,
                "source_trial_id": "trial-1",
                "lifecycle_outcome": "completed",
                "lifecycle_reason_code": "agent-terminal-complete",
                "retry_eligible": False,
                "completed_export_sha256": available_digest(),
                "candidate_tree_sha256": available_digest(),
                "artifact_manifest_sha256": available_digest(),
            }
        ],
        "selected_attempt_id": ATTEMPT,
        "evaluation": {
            "validity": "valid",
            "behavioral_decision": "pass",
            "gate_decision": "pass",
            "strict_reload": verified_reload(),
        },
        "test_runner": {
            "authority_id": "tiny-calculator-test-runner",
            "authority_version": "1",
            "authority_sha256": DIGEST,
            "test_plan_sha256": available_digest(),
            "test_results_sha256": available_digest(),
            "process_receipt_sha256": available_digest(),
            "resource_receipt_sha256": available_digest(),
        },
        "diagnostics": {
            "duration": {
                "status": "available",
                "value": 12,
                "unit": "milliseconds",
                "source_receipt_sha256": DIGEST,
            },
            "peak_memory": missing("not-collected"),
            "cpu_time": missing("not-collected"),
            "input_tokens": missing(),
            "output_tokens": missing(),
            "total_tokens": missing(),
            "network_capable_commands": missing(),
            "runtime_boundary_observation_sha256": missing(),
        },
        "offline_rebuild": {
            "status": "available",
            "experiment_spec": {"path": "examples/tiny-calculator-v1.json", "sha256": DIGEST},
            "completed_export": {"path": "frozen/attempt-1", "sha256": DIGEST},
            "adapted_bundle": {"path": "rebuilt/bundle", "sha256": DIGEST},
            "evaluation_output": {"path": "rebuilt/evaluation", "sha256": DIGEST},
            "commands": [
                {
                    "purpose": "adapt",
                    "working_directory": ".",
                    "argv": ["uv", "run", "python", "scripts/evaluate_frozen.py", "adapt"],
                },
                {
                    "purpose": "evaluate",
                    "working_directory": ".",
                    "argv": ["uv", "run", "python", "scripts/evaluate_frozen.py", "evaluate"],
                },
                {
                    "purpose": "strict-reload",
                    "working_directory": ".",
                    "argv": ["uv", "run", "python", "scripts/evaluate_frozen.py", "reload"],
                },
            ],
        },
    }


def test_report_json_is_canonical_and_markdown_is_deterministic(tmp_path: Path) -> None:
    first = materialize_run_report(valid_payload())
    second = materialize_run_report(deepcopy(valid_payload()))

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.markdown() == second.markdown()
    assert "JSON model remains authoritative" not in first.markdown()
    assert "`pass`" in first.markdown()
    assert "missing (not-collected)" in first.markdown()

    destination = tmp_path / "report"
    publish_run_report(first, destination)
    assert RunReport.from_file(destination / "run-report.json") == first
    assert (destination / "run-report.md").read_text(encoding="utf-8") == first.markdown()
    with pytest.raises(ReportError, match="must not already exist"):
        publish_run_report(first, destination)


def test_checked_in_schema_accepts_the_authoritative_report() -> None:
    report = materialize_run_report(valid_payload())
    schema_path = Path(__file__).parents[2] / "schemas" / "run-report-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report.model_dump(mode="json"))


@pytest.mark.parametrize(
    "mutation", ("unknown", "identity", "host-path", "secret", "env", "markdown-injection")
)
def test_report_rejects_nonportable_or_unbound_content(mutation: str) -> None:
    payload = valid_payload()
    if mutation == "unknown":
        payload["created_at"] = "2026-08-24T00:00:00Z"
    elif mutation == "identity":
        report = materialize_run_report(payload)
        bound = report.model_dump(mode="json")
        bound["selected_attempt_id"] = "c" * 64
        with pytest.raises(ValueError, match="selected attempt"):
            RunReport.model_validate(bound)
        return
    else:
        rebuild = payload["offline_rebuild"]
        assert isinstance(rebuild, dict)
        commands = rebuild["commands"]
        assert isinstance(commands, list)
        command = commands[0]
        assert isinstance(command, dict)
        argv = command["argv"]
        assert isinstance(argv, list)
        if mutation == "host-path":
            argv.append("/Users/example/private/export")
        elif mutation == "secret":
            argv.append("sk-proj-" + "ThisIsProvablyFakeButLooksLikeASecret123")
        elif mutation == "env":
            argv.append("CODEX_AUTH_JSON_PATH=auth.json")
        else:
            argv.append("`\n## Forged conclusion")
    with pytest.raises(ReportError):
        materialize_run_report(payload)


def test_missing_data_must_be_explicit_and_cannot_invent_zero() -> None:
    payload = valid_payload()
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, dict)
    diagnostics["peak_memory"] = None
    with pytest.raises(ReportError):
        materialize_run_report(payload)

    payload = valid_payload()
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, dict)
    diagnostics["peak_memory"] = {
        "status": "available",
        "value": 0,
        "unit": "tokens",
        "source_receipt_sha256": DIGEST,
    }
    with pytest.raises(ReportError, match="peak_memory must use bytes"):
        materialize_run_report(payload)


def test_retry_history_cannot_hide_or_select_an_unauthorized_attempt() -> None:
    payload = valid_payload()
    attempts = payload["attempts"]
    assert isinstance(attempts, list)
    second = deepcopy(attempts[0])
    assert isinstance(second, dict)
    second.update(
        {
            "ordinal": 2,
            "attempt_id": "c" * 64,
            "predecessor_attempt_id": ATTEMPT,
            "retry_delay_seconds": 10,
        }
    )
    attempts.append(second)
    payload["selected_attempt_id"] = "c" * 64
    with pytest.raises(ReportError, match="authorized retry"):
        materialize_run_report(payload)


def test_loader_rejects_noncanonical_json(tmp_path: Path) -> None:
    report = materialize_run_report(valid_payload())
    path = tmp_path / "run-report.json"
    payload = report.model_dump(mode="json")
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
    with pytest.raises(ReportError, match="not canonical"):
        RunReport.from_file(path)
