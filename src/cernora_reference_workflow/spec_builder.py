"""Materialize the closed set of approved tiny-calculator ExperimentSpec identities."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    closed_regular_tree,
    sha256_bytes,
    sha256_file,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec, materialize_experiment_spec
from cernora_reference_workflow.profile import create_profile
from cernora_reference_workflow.runtime_policy import RUNTIME_CONFIGURATION_SHA256
from cernora_reference_workflow.test_runner import TestPlan

WHEEL_SHA256 = "01de19a484172cc8e3940792b90de04683da600320d154fff18b0a717738a2df"
BASE_IMAGE = (
    "cernora-reference/codex-runtime@sha256:"
    "0e9ac928b97a83c54663f1086576039175b9bd4513b7d8d97f8173af62416788"
)
TASK_IMAGE_SHA256 = {
    "tiny-calculator-v1": "57016ac36d8ad1a402b40de4a370be93e2ac392fa6e093b203aa2676e34debd2",
    "tiny-calculator-v2": "2ba88de8936e2f396f0e33a6ae11a253fb6b7e779226a82ed2ed727543c4f430",
}
TASK_FILES = (
    "environment/.dockerignore",
    "environment/Dockerfile",
    "environment/pyproject.toml",
    "environment/src/calc.py",
    "instruction.md",
    "task.json",
    "task.toml",
    "tests/run_tests.py",
    "tests/test-plan.json",
    "tests/test.sh",
)
DEFAULT_AGENT_TIMEOUT_MULTIPLIER = 1.0
TIMEOUT_AGENT_TIMEOUT_MULTIPLIER = 0.01


def _harness_configuration(
    agent_timeout_multiplier: float,
    *,
    operator_interrupt: bool,
) -> dict[str, object]:
    return {
        "agent": "cernora_reference_workflow.runtime_agent:TelemetryDisabledCodex",
        "agent_setup_timeout_multiplier": 4,
        "agent_timeout_multiplier": agent_timeout_multiplier,
        "delete_environment": True,
        "environment": "docker",
        "force_build": False,
        "harbor_native_retries": 0,
        "install_only": False,
        "n_attempts": 1,
        "n_concurrent_trials": 1,
        "override_cpus": 2,
        "override_memory_mb": 4096,
        "operator_interrupt": operator_interrupt,
    }


HARNESS_CONFIGURATION_SHA256 = sha256_bytes(
    canonical_json_bytes(
        _harness_configuration(DEFAULT_AGENT_TIMEOUT_MULTIPLIER, operator_interrupt=False)
    )
)
TIMEOUT_HARNESS_CONFIGURATION_SHA256 = sha256_bytes(
    canonical_json_bytes(
        _harness_configuration(TIMEOUT_AGENT_TIMEOUT_MULTIPLIER, operator_interrupt=False)
    )
)
INTERRUPTION_HARNESS_CONFIGURATION_SHA256 = sha256_bytes(
    canonical_json_bytes(
        _harness_configuration(DEFAULT_AGENT_TIMEOUT_MULTIPLIER, operator_interrupt=True)
    )
)


def _task_content_sha256(task_root: Path) -> str:
    files = closed_regular_tree(task_root)
    portable_files = {
        relative: path
        for relative, path in files.items()
        if "__pycache__" not in Path(relative).parts and path.suffix not in {".pyc", ".pyo"}
    }
    if set(portable_files) != set(TASK_FILES):
        missing = sorted(set(TASK_FILES) - set(portable_files))
        extra = sorted(set(portable_files) - set(TASK_FILES))
        raise ContractError(
            f"task tree does not match frozen file set; missing={missing}, extra={extra}"
        )
    payload = {
        "files": [
            {
                "path": relative,
                "sha256": sha256_file(portable_files[relative]),
                "size_bytes": portable_files[relative].stat().st_size,
            }
            for relative in TASK_FILES
        ]
    }
    return sha256_bytes(canonical_json_bytes(payload))


def _build_spec(
    repository_root: Path,
    *,
    task_id: Literal["tiny-calculator-v1", "tiny-calculator-v2"],
    task_version: Literal["1", "2"],
    timeout_seconds: int = 300,
    agent_timeout_multiplier: float = DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
    operator_interrupt: bool = False,
) -> ExperimentSpec:
    approved_variants = {
        (300, DEFAULT_AGENT_TIMEOUT_MULTIPLIER, False): HARNESS_CONFIGURATION_SHA256,
        (3, TIMEOUT_AGENT_TIMEOUT_MULTIPLIER, False): TIMEOUT_HARNESS_CONFIGURATION_SHA256,
        (
            300,
            DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
            True,
        ): INTERRUPTION_HARNESS_CONFIGURATION_SHA256,
    }
    try:
        harness_configuration_sha256 = approved_variants[
            (timeout_seconds, agent_timeout_multiplier, operator_interrupt)
        ]
    except KeyError as exc:
        raise ContractError("unapproved tiny-calculator lifecycle configuration") from exc
    task_root = repository_root / f"tasks/{task_id}"
    instruction = task_root / "instruction.md"
    test_plan_path = task_root / "tests/test-plan.json"
    plan = TestPlan.model_validate_json(test_plan_path.read_bytes(), strict=True)
    if test_plan_path.read_bytes() != canonical_json_bytes(plan.model_dump(mode="json")):
        raise ContractError("task Test Plan is not canonical JSON")
    expected_authority_id = {
        "tiny-calculator-v1": "tiny-calculator-test-runner",
        "tiny-calculator-v2": "tiny-calculator-v2-test-runner",
    }[task_id]
    if plan.authority_id != expected_authority_id:
        raise ContractError("task ID and Test Runner authority do not match")
    profile = create_profile()
    profile_sha256 = sha256_bytes(
        canonical_json_bytes(profile.authority.model_dump(mode="json", exclude_none=False))
    )
    instruction_sha256 = sha256_file(instruction)
    return materialize_experiment_spec(
        {
            "schema_version": "cernora.reference.experiment-spec/v1",
            "task": {
                "task_id": task_id,
                "task_version": task_version,
                "content_sha256": _task_content_sha256(task_root),
                "prompt_sha256": instruction_sha256,
                "instruction_sha256": instruction_sha256,
                "allowed_paths": ["src/calc.py"],
                "protected_paths": ["pyproject.toml", "tests"],
            },
            "container": {
                "image": (f"cernora-reference/{task_id}@sha256:{TASK_IMAGE_SHA256[task_id]}"),
                "build_base_image": BASE_IMAGE,
                "platform": "linux/arm64",
            },
            "harness": {
                "name": "harbor",
                "version": "0.16.1",
                "configuration_sha256": harness_configuration_sha256,
            },
            "runtime": {
                "name": "codex",
                "version": "0.148.0",
                "configuration_sha256": RUNTIME_CONFIGURATION_SHA256,
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
            },
            "prompt_sha256": instruction_sha256,
            "instruction_sha256": instruction_sha256,
            "limits": {
                "agent_setup_timeout_seconds": 1440,
                "timeout_seconds": timeout_seconds,
                "memory_mebibytes": 4096,
                "cpu_millis": 2000,
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
                "authority_id": plan.authority_id,
                "authority_version": "1",
                "authority_sha256": plan.authority_sha256,
                "test_plan_sha256": sha256_file(test_plan_path),
                "test_source_sha256": plan.test_source_sha256,
                "command": list(plan.command),
                "working_directory": "candidate",
            },
            "profile": {
                "profile_id": "cernora-reference-coding-v1",
                "profile_version": "1.0.0",
                "authority_sha256": profile_sha256,
            },
            "workflow": {
                "exporter": "completed-export/v1",
                "adapter": "cernora-reference-adapter/v1",
                "report": "cernora-reference-run-report/v1",
            },
            "cernora": {
                "package_version": "0.1.2",
                "wheel_sha256": WHEEL_SHA256,
            },
        }
    )


def build_tiny_calculator_spec(
    repository_root: Path,
    *,
    timeout_seconds: int = 300,
    agent_timeout_multiplier: float = DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
    operator_interrupt: bool = False,
) -> ExperimentSpec:
    return _build_spec(
        repository_root,
        task_id="tiny-calculator-v1",
        task_version="1",
        timeout_seconds=timeout_seconds,
        agent_timeout_multiplier=agent_timeout_multiplier,
        operator_interrupt=operator_interrupt,
    )


def build_tiny_calculator_v2_spec(
    repository_root: Path,
    *,
    timeout_seconds: int = 300,
    agent_timeout_multiplier: float = DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
) -> ExperimentSpec:
    return _build_spec(
        repository_root,
        task_id="tiny-calculator-v2",
        task_version="2",
        timeout_seconds=timeout_seconds,
        agent_timeout_multiplier=agent_timeout_multiplier,
    )


__all__ = [
    "DEFAULT_AGENT_TIMEOUT_MULTIPLIER",
    "HARNESS_CONFIGURATION_SHA256",
    "INTERRUPTION_HARNESS_CONFIGURATION_SHA256",
    "TIMEOUT_AGENT_TIMEOUT_MULTIPLIER",
    "TIMEOUT_HARNESS_CONFIGURATION_SHA256",
    "build_tiny_calculator_spec",
    "build_tiny_calculator_v2_spec",
]
