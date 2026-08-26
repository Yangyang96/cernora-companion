from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.spec_builder import (
    HARNESS_CONFIGURATION_SHA256,
    INTERRUPTION_HARNESS_CONFIGURATION_SHA256,
    TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    TIMEOUT_HARNESS_CONFIGURATION_SHA256,
    build_tiny_calculator_spec,
    build_tiny_calculator_v2_spec,
)

ROOT = Path(__file__).resolve().parents[2]


def test_builder_matches_checked_in_canonical_example() -> None:
    spec = build_tiny_calculator_spec(ROOT)
    example = ROOT / "examples/tiny-calculator-v1.json"
    assert example.read_bytes() == spec.canonical_bytes()
    assert ExperimentSpec.from_file(example) == spec


def test_builder_binds_effective_setup_and_harness_policy() -> None:
    spec = build_tiny_calculator_spec(ROOT)
    assert spec.limits.agent_setup_timeout_seconds == 1440
    assert spec.limits.memory_mebibytes == 4096
    assert spec.limits.cpu_millis == 2000
    assert spec.harness.configuration_sha256 == HARNESS_CONFIGURATION_SHA256


def test_builder_materializes_only_the_approved_timeout_variant() -> None:
    spec = build_tiny_calculator_spec(
        ROOT,
        timeout_seconds=3,
        agent_timeout_multiplier=TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    )
    example = ROOT / "examples/tiny-calculator-v1-timeout.json"
    assert example.read_bytes() == spec.canonical_bytes()
    assert ExperimentSpec.from_file(example) == spec
    assert spec.limits.timeout_seconds == 3
    assert spec.harness.configuration_sha256 == TIMEOUT_HARNESS_CONFIGURATION_SHA256

    with pytest.raises(ContractError, match="unapproved"):
        build_tiny_calculator_spec(ROOT, timeout_seconds=2, agent_timeout_multiplier=0.01)


def test_builder_matches_the_versioned_harder_task_example() -> None:
    spec = build_tiny_calculator_v2_spec(ROOT)
    example = ROOT / "examples/tiny-calculator-v2.json"
    assert example.read_bytes() == spec.canonical_bytes()
    assert ExperimentSpec.from_file(example) == spec
    assert spec.task.task_id == "tiny-calculator-v2"
    assert spec.task.task_version == "2"
    assert spec.test_runner.authority_id == "tiny-calculator-v2-test-runner"


def test_builder_materializes_the_harder_task_timeout_variant() -> None:
    spec = build_tiny_calculator_v2_spec(
        ROOT,
        timeout_seconds=3,
        agent_timeout_multiplier=TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    )
    example = ROOT / "examples/tiny-calculator-v2-timeout.json"
    assert example.read_bytes() == spec.canonical_bytes()
    assert ExperimentSpec.from_file(example) == spec
    assert spec.limits.timeout_seconds == 3
    assert spec.harness.configuration_sha256 == TIMEOUT_HARNESS_CONFIGURATION_SHA256


def test_builder_binds_operator_interruption_to_a_distinct_identity() -> None:
    normal = build_tiny_calculator_spec(ROOT)
    interruption = build_tiny_calculator_spec(ROOT, operator_interrupt=True)
    example = ROOT / "examples/tiny-calculator-v1-interruption.json"
    assert example.read_bytes() == interruption.canonical_bytes()
    assert normal.experiment_id != interruption.experiment_id
    assert interruption.harness.configuration_sha256 == INTERRUPTION_HARNESS_CONFIGURATION_SHA256
