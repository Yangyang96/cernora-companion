from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.experiment_spec import materialize_experiment_spec

DIGEST = "0" * 64


def valid_payload() -> dict[str, object]:
    return {
        "schema_version": "cernora.reference.experiment-spec/v1",
        "task": {
            "task_id": "tiny-calculator-v1",
            "task_version": "1",
            "content_sha256": DIGEST,
            "prompt_sha256": DIGEST,
            "instruction_sha256": DIGEST,
            "allowed_paths": ["src/calc.py"],
            "protected_paths": ["tests", "pyproject.toml"],
        },
        "container": {
            "image": f"python:3.12.13-slim-bookworm@sha256:{DIGEST}",
            "build_base_image": f"python:3.12.13-slim-bookworm@sha256:{DIGEST}",
            "platform": "linux/arm64",
        },
        "harness": {"name": "harbor", "version": "0.16.1", "configuration_sha256": DIGEST},
        "runtime": {
            "name": "codex",
            "version": "0.148.0",
            "configuration_sha256": DIGEST,
            "model": "gpt-5.6-terra",
            "reasoning_effort": "medium",
        },
        "prompt_sha256": DIGEST,
        "instruction_sha256": DIGEST,
        "limits": {
            "agent_setup_timeout_seconds": 1440,
            "timeout_seconds": 300,
            "memory_mebibytes": 1024,
            "cpu_millis": 1000,
        },
        "network": {"provider_egress": "required-allowed", "web_search": False},
        "retry": {
            "max_retries": 1,
            "delay_seconds": 10,
            "jitter": False,
            "eligible_states": [
                "infrastructure-start-failure",
                "transient-provider-pre-terminal",
            ],
        },
        "test_runner": {
            "authority_id": "tiny-calculator-test-runner",
            "authority_version": "1",
            "authority_sha256": DIGEST,
            "test_plan_sha256": DIGEST,
            "test_source_sha256": DIGEST,
            "command": ["python", "-m", "pytest", "-q"],
            "working_directory": "candidate",
        },
        "profile": {
            "profile_id": "cernora-reference-coding-v1",
            "profile_version": "1.0.0",
            "authority_sha256": DIGEST,
        },
        "workflow": {
            "exporter": "completed-export/v1",
            "adapter": "cernora-reference-adapter/v1",
            "report": "cernora-reference-run-report/v1",
        },
        "cernora": {"package_version": "0.1.2", "wheel_sha256": DIGEST},
    }


def test_materialized_identity_is_deterministic() -> None:
    first = materialize_experiment_spec(valid_payload())
    second = materialize_experiment_spec(deepcopy(valid_payload()))
    assert first == second
    assert first.canonical_bytes() == second.canonical_bytes()


@pytest.mark.parametrize("mutation", ("unknown", "wrong_pin", "auth", "unsafe_path"))
def test_experiment_spec_fails_closed(mutation: str) -> None:
    payload = valid_payload()
    if mutation == "unknown":
        payload["created_at"] = "2026-08-21T00:00:00Z"
    elif mutation == "wrong_pin":
        assert isinstance(payload["runtime"], dict)
        payload["runtime"]["version"] = "0.149.0"
    elif mutation == "auth":
        assert isinstance(payload["runtime"], dict)
        payload["runtime"]["auth_path"] = "/Users/example/auth.json"
    else:
        assert isinstance(payload["task"], dict)
        payload["task"]["allowed_paths"] = ["../calc.py"]
    with pytest.raises((ValidationError, ValueError)):
        materialize_experiment_spec(payload)
