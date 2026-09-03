from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from cernora import BootstrapPlan, PassKPlan, component_identity
from pydantic import JsonValue, ValidationError

from cernora_reference_workflow.common import ContractError, canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.controlled_experiment_spec import (
    ACCEPTED_CORE_0_1_4_WHEEL_SHA256,
    ControlledExperimentSpecV2,
    DatasetCaseAuthority,
    EvaluationCaseIdentitySource,
    materialize_authority_source,
    materialize_controlled_experiment_spec,
    materialize_dataset_authority,
    materialize_expected_evaluation_authority,
    materialize_expected_evaluation_policy,
    materialize_statistical_policy,
)

DIGEST = "0" * 64


def _source(source_id: str, payload: JsonValue) -> dict[str, object]:
    return materialize_authority_source(source_id, payload).model_dump(mode="json")


def valid_payload(
    *, case_id: str = "repair-case-1", configuration_id: str = "baseline", prompt: str = "repair"
) -> dict[str, object]:
    task_source = materialize_authority_source(
        "task", {"case": case_id, "files": {"src/main.py": "def value(): return 0\n"}}
    )
    task_prompt_source = materialize_authority_source(
        "task-prompt", {"case": case_id, "text": "Fix the supplied project."}
    )
    task_instruction_source = materialize_authority_source(
        "task-instruction", {"case": case_id, "text": "Satisfy the tests."}
    )
    prompt_source = materialize_authority_source("treatment-prompt", {"text": prompt})
    instruction_source = materialize_authority_source(
        "treatment-instruction", {"text": "Change only allowed source files."}
    )
    harness_source = materialize_authority_source("harness", {"mode": "single-attempt"})
    runtime_source = materialize_authority_source(
        "runtime", {"approval": "never", "sandbox": "workspace-write"}
    )
    authority_source = materialize_authority_source(
        "test-authority", {"implementation": "pytest-authority-v1"}
    )
    test_plan_source = materialize_authority_source(
        "test-plan", {"command": ["python", "-m", "pytest", "-q"]}
    )
    test_source = materialize_authority_source(
        "test-source", {"tests/test_main.py": "def test_value(): assert value() == 1\n"}
    )
    profile_source = materialize_authority_source(
        "profile", {"id": "cernora-reference-coding-v1", "version": "1.0.0"}
    )
    projection_version = "1"
    imported_projection = {
        "name": "imported_projection",
        "version": projection_version,
        "sha256": sha256_bytes(
            canonical_json_bytes({"name": "imported_projection", "version": projection_version})
        ),
        "digest_kind": "identity",
    }
    scorer = component_identity("scorer", "coding-score/v1")
    gate = component_identity("gate_policy", "coding-gate/v1")
    profile_identity = {
        "profile_id": "cernora-reference-coding-v1",
        "profile_version": "1.0.0",
        "sha256": profile_source.source_sha256,
    }
    case_identity = {
        "case_id": case_id,
        "case_version": "1",
        "case_set": "synthetic-python-repair",
        "sha256": task_source.source_sha256,
    }
    expected_authority = materialize_expected_evaluation_authority(
        {
            "schema_version": "agent.evaluator.imported-evaluation-authority/v1",
            "profile": profile_identity,
            "case": case_identity,
            "fixtures": [{"fixture_id": "workspace", "path": "fixture", "sha256": DIGEST}],
            "projection": imported_projection,
            "scorer": scorer.model_dump(mode="json"),
            "case_gate": gate.model_dump(mode="json"),
        }
    )
    expected_policy = materialize_expected_evaluation_policy(
        {
            "schema_version": "agent.evaluator.comparison-evaluation-policy/v1",
            "profile": profile_identity,
            "projection": imported_projection,
            "scorer": scorer.model_dump(mode="json"),
            "case_gate": gate.model_dump(mode="json"),
        }
    )
    bootstrap = BootstrapPlan(
        method="case-clustered-paired-bootstrap/v1",
        confidence_basis_points=9500,
        resamples=10000,
        percentile="nearest_rank_closed",
        seed_source="comparison_input_sha256",
    )
    pass_k = PassKPlan(k=3, independent_trials=True)
    statistics = materialize_statistical_policy(bootstrap=bootstrap, pass_k=pass_k)
    task_image = f"python:3.12@sha256:{DIGEST}"
    build_base_image = f"python:3.12@sha256:{DIGEST}"
    dataset = materialize_dataset_authority(
        (
            DatasetCaseAuthority(
                case=EvaluationCaseIdentitySource.model_validate(case_identity),
                task_source_sha256=task_source.source_sha256,
                task_prompt_sha256=task_prompt_source.source_sha256,
                task_instruction_sha256=task_instruction_source.source_sha256,
                allowed_paths=("src/main.py",),
                protected_paths=("pyproject.toml", "tests"),
                task_image=task_image,
                build_base_image=build_base_image,
                test_authority_id="synthetic-python-test-runner",
                test_authority_version="1",
                test_authority_sha256=authority_source.source_sha256,
                test_plan_sha256=test_plan_source.source_sha256,
                test_source_sha256=test_source.source_sha256,
                test_command=("python", "-m", "pytest", "-q"),
                test_working_directory="candidate",
                fixtures=expected_authority.fixtures,
            ),
        )
    )
    return {
        "schema_version": "cernora.reference.controlled-experiment-spec/v2",
        "configuration_id": configuration_id,
        "task": {
            "task_id": case_id,
            "task_version": "1",
            "case_set": "synthetic-python-repair",
            "content_sha256": task_source.source_sha256,
            "task_source": task_source.model_dump(mode="json"),
            "prompt_sha256": task_prompt_source.source_sha256,
            "prompt_source": task_prompt_source.model_dump(mode="json"),
            "instruction_sha256": task_instruction_source.source_sha256,
            "instruction_source": task_instruction_source.model_dump(mode="json"),
            "allowed_paths": ["src/main.py"],
            "protected_paths": ["pyproject.toml", "tests"],
        },
        "container": {
            "image": task_image,
            "build_base_image": build_base_image,
            "platform": "linux/arm64",
        },
        "harness": {
            "name": "harbor",
            "version": "0.16.1",
            "configuration_sha256": harness_source.source_sha256,
            "configuration_source": harness_source.model_dump(mode="json"),
        },
        "runtime": {
            "name": "pi",
            "version": "0.84.4",
            "configuration_sha256": runtime_source.source_sha256,
            "configuration_source": runtime_source.model_dump(mode="json"),
            "model": "deepseek/deepseek-v4-flash",
            "reasoning_effort": "medium",
        },
        "prompt_sha256": prompt_source.source_sha256,
        "prompt_source": prompt_source.model_dump(mode="json"),
        "instruction_sha256": instruction_source.source_sha256,
        "instruction_source": instruction_source.model_dump(mode="json"),
        "tool_schema_source": _source("tool-schema", {"tools": ["shell"]}),
        "generation_configuration_source": _source(
            "generation-configuration", {"temperature": 0, "reasoning": "medium"}
        ),
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
            "authority_id": "synthetic-python-test-runner",
            "authority_version": "1",
            "authority_sha256": authority_source.source_sha256,
            "authority_source": authority_source.model_dump(mode="json"),
            "test_plan_sha256": test_plan_source.source_sha256,
            "test_plan_source": test_plan_source.model_dump(mode="json"),
            "test_source_sha256": test_source.source_sha256,
            "test_source": test_source.model_dump(mode="json"),
            "command": ["python", "-m", "pytest", "-q"],
            "working_directory": "candidate",
        },
        "profile": {
            "profile_id": "cernora-reference-coding-v1",
            "profile_version": "1.0.0",
            "authority_sha256": profile_source.source_sha256,
            "authority_source": profile_source.model_dump(mode="json"),
        },
        "expected_evaluation_authority": expected_authority.model_dump(mode="json"),
        "expected_evaluation_policy": expected_policy.model_dump(mode="json"),
        "dataset_authority": dataset.model_dump(mode="json"),
        "statistical_policy": statistics.model_dump(mode="json"),
        "workflow": {
            "exporter": "completed-export/v1",
            "adapter": "cernora-reference-adapter/v1",
            "report": "cernora-reference-run-report/v1",
        },
        "cernora": {
            "package_version": "0.1.4",
            "wheel_sha256": ACCEPTED_CORE_0_1_4_WHEEL_SHA256,
        },
    }


def test_v2_identity_is_exactly_the_materialized_core_authority() -> None:
    spec = materialize_controlled_experiment_spec(valid_payload())

    assert spec.experiment_id == spec.core_authority().experiment_id
    assert spec.core_authority().projection == spec.core_projection()
    assert spec == materialize_controlled_experiment_spec(deepcopy(valid_payload()))


def test_opaque_runtime_configuration_feeds_tool_and_generation_projections() -> None:
    first = materialize_controlled_experiment_spec(valid_payload())
    changed = valid_payload()
    runtime = changed["runtime"]
    assert isinstance(runtime, dict)
    source = materialize_authority_source(
        "runtime", {"approval": "never", "sandbox": "workspace-write", "mode": "changed"}
    )
    runtime["configuration_source"] = source.model_dump(mode="json")
    runtime["configuration_sha256"] = source.source_sha256
    second = materialize_controlled_experiment_spec(changed)

    assert first.core_projection().tool_schema_sha256 != second.core_projection().tool_schema_sha256
    assert (
        first.core_projection().generation_configuration_sha256
        != second.core_projection().generation_configuration_sha256
    )
    assert first.experiment_id != second.experiment_id


@pytest.mark.parametrize(
    ("section", "field", "replacement", "projection_field"),
    (
        ("container", "platform", "linux/amd64", "resources_sha256"),
        ("limits", "timeout_seconds", 301, "timeout_sha256"),
        ("runtime", "model", "gpt-5.6-sol", "model_sha256"),
        ("workflow", "report", "cernora-reference-run-report/v2", "report_contract_sha256"),
    ),
)
def test_hidden_execution_fields_change_a_core_projection(
    section: str, field: str, replacement: object, projection_field: str
) -> None:
    first = materialize_controlled_experiment_spec(valid_payload())
    changed = valid_payload()
    nested = changed[section]
    assert isinstance(nested, dict)
    nested[field] = replacement
    second = materialize_controlled_experiment_spec(changed)

    assert getattr(first.core_projection(), projection_field) != getattr(
        second.core_projection(), projection_field
    )


def test_v2_rejects_unaccepted_core_wheel_digest() -> None:
    payload = valid_payload()
    cernora = payload["cernora"]
    assert isinstance(cernora, dict)
    cernora["wheel_sha256"] = DIGEST

    with pytest.raises(ValidationError, match="wheel_sha256"):
        materialize_controlled_experiment_spec(payload)


@pytest.mark.parametrize("duplicate", ("fixture_id", "path"))
def test_expected_authority_rejects_duplicate_fixture_dimensions(duplicate: str) -> None:
    source = valid_payload()["expected_evaluation_authority"]
    assert isinstance(source, dict)
    source.pop("authority_id")
    source.pop("authority_sha256")
    fixtures = source["fixtures"]
    assert isinstance(fixtures, list)
    second = dict(fixtures[0])
    if duplicate == "fixture_id":
        second["path"] = "fixture-two"
    else:
        second["fixture_id"] = "workspace-two"
    fixtures.append(second)

    expected = "fixture IDs must be unique" if duplicate == "fixture_id" else "fixture paths"
    with pytest.raises(ValidationError, match=expected):
        materialize_expected_evaluation_authority(source)


@pytest.mark.parametrize("duplicate", ("fixture_id", "path"))
def test_dataset_case_rejects_duplicate_fixture_dimensions(duplicate: str) -> None:
    dataset = valid_payload()["dataset_authority"]
    assert isinstance(dataset, dict)
    cases = dataset["cases"]
    assert isinstance(cases, list)
    case = deepcopy(cases[0])
    fixtures = case["fixtures"]
    assert isinstance(fixtures, list)
    second = dict(fixtures[0])
    if duplicate == "fixture_id":
        second["path"] = "fixture-two"
    else:
        second["fixture_id"] = "workspace-two"
    fixtures.append(second)

    expected = "fixture IDs must be unique" if duplicate == "fixture_id" else "fixture paths"
    with pytest.raises(ValidationError, match=expected):
        DatasetCaseAuthority.model_validate(case)


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown",
        "source_digest",
        "authority",
        "unbound_command",
        "unbound_task_policy",
        "legacy_id",
    ),
)
def test_v2_fails_closed_on_tamper_and_legacy_identity(mutation: str) -> None:
    payload = valid_payload()
    if mutation == "unknown":
        payload["created_at"] = "2026-08-27T00:00:00Z"
    elif mutation == "source_digest":
        prompt_source = payload["prompt_source"]
        assert isinstance(prompt_source, dict)
        prompt_source["source_sha256"] = DIGEST
    elif mutation == "authority":
        authority = payload["expected_evaluation_authority"]
        assert isinstance(authority, dict)
        authority["authority_sha256"] = DIGEST
    elif mutation == "unbound_command":
        test_runner = payload["test_runner"]
        assert isinstance(test_runner, dict)
        test_runner["command"] = ["python", "-m", "unittest"]
    elif mutation == "unbound_task_policy":
        task = payload["task"]
        assert isinstance(task, dict)
        task["allowed_paths"] = ["src/main.py", "src/other.py"]
    else:
        payload["experiment_id"] = DIGEST
    with pytest.raises((ContractError, ValidationError, ValueError)):
        if mutation == "legacy_id":
            ControlledExperimentSpecV2.model_validate(payload)
        else:
            materialize_controlled_experiment_spec(payload)


def test_v2_file_requires_canonical_bytes(tmp_path: Path) -> None:
    spec = materialize_controlled_experiment_spec(valid_payload())
    canonical = tmp_path / "experiment-spec-v2.json"
    canonical.write_bytes(spec.canonical_bytes())
    assert ControlledExperimentSpecV2.from_file(canonical) == spec

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text("{\n" + spec.canonical_bytes().decode()[1:], encoding="utf-8")
    with pytest.raises(ContractError, match="not canonical"):
        ControlledExperimentSpecV2.from_file(noncanonical)
