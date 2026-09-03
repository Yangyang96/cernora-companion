"""Build the exact authority-bound M4 RunPlan and ComparisonPlan."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from cernora import BootstrapPlan, PassKPlan, component_identity
from pydantic import Field, JsonValue, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
    validate_sha256,
)
from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    materialize_comparison_plan,
    materialize_treatment_declaration,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    ACCEPTED_CORE_0_1_4_WHEEL_SHA256,
    CanonicalAuthoritySource,
    ControlledExperimentSpecV2,
    DatasetCaseAuthority,
    Digest,
    EvaluationCaseIdentitySource,
    Identifier,
    StrictV2Contract,
    materialize_authority_source,
    materialize_controlled_experiment_spec,
    materialize_dataset_authority,
    materialize_expected_evaluation_authority,
    materialize_expected_evaluation_policy,
    materialize_statistical_policy,
)
from cernora_reference_workflow.controlled_profile import (
    GATE_VERSION,
    PROFILE_ID,
    PROFILE_VERSION,
    PROJECTION_VERSION,
    SCORER_VERSION,
    build_controlled_profile_authority,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.improvement_loop import CandidateFreeze
from cernora_reference_workflow.runtime_policy import (
    PI_RUNTIME_ENVIRONMENT,
    PI_RUNTIME_INSTALLATION,
    PI_VERSION,
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
)

_BASELINE_CONFIGURATION = "baseline"
_CANDIDATE_CONFIGURATION = "candidate"
_PLATFORM = "linux/arm64"
_MODEL = "deepseek/deepseek-v4-flash"
_REASONING_EFFORT = "medium"


class M4TaskImageAuthority(StrictV2Contract):
    case_id: Identifier
    image: str = Field(min_length=1)

    @model_validator(mode="after")
    def immutable_case_image(self) -> Self:
        expected_prefix = f"cernora-reference/m4-{self.case_id}@sha256:"
        if not self.image.startswith(expected_prefix):
            raise ValueError("M4 task image name must bind its Case ID")
        validate_sha256(self.image.removeprefix(expected_prefix), label="M4 task image digest")
        return self


class M4ImageAuthoritySet(StrictV2Contract):
    schema_version: Literal["cernora.reference.m4-image-authorities/v1"]
    authority_set_id: Digest
    build_base_image: str = Field(min_length=1)
    platform: Literal["linux/arm64"]
    images: Annotated[tuple[M4TaskImageAuthority, ...], Field(min_length=9, max_length=9)]

    @field_validator("images", mode="before")
    @classmethod
    def tuple_images(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def canonical_authority_set(self) -> Self:
        case_ids = tuple(item.case_id for item in self.images)
        if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("M4 image authorities must be sorted and unique")
        marker = "@sha256:"
        if marker not in self.build_base_image:
            raise ValueError("M4 build base image must be immutable")
        validate_sha256(
            self.build_base_image.rsplit(marker, 1)[1], label="M4 build base image digest"
        )
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"authority_set_id"})
        )
        if self.authority_set_id != expected:
            raise ValueError("M4 image authority set identity mismatch")
        return self

    @classmethod
    def from_bytes(cls, data: bytes) -> M4ImageAuthoritySet:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise ContractError("M4 image authority set must be one JSON object")
        value = cls.model_validate(payload)
        if value.canonical_bytes() != data:
            raise ContractError("M4 image authority set is not canonical JSON")
        return value

    @classmethod
    def from_file(cls, path: Path) -> M4ImageAuthoritySet:
        return cls.from_bytes(read_regular_file_bytes(path))

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def materialize_m4_image_authority_set(
    *, build_base_image: str, images: Mapping[str, str]
) -> M4ImageAuthoritySet:
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.m4-image-authorities/v1",
        "build_base_image": build_base_image,
        "platform": _PLATFORM,
        "images": [
            {"case_id": case_id, "image": image} for case_id, image in sorted(images.items())
        ],
    }
    payload["authority_set_id"] = canonical_content_id(payload, excluded=frozenset())
    return M4ImageAuthoritySet.model_validate(payload)


def _source(source_id: str, payload: JsonValue) -> CanonicalAuthoritySource:
    return materialize_authority_source(source_id, payload)


def _runtime_source() -> CanonicalAuthoritySource:
    source = _source(
        "runtime",
        cast(
            JsonValue,
            {
                "pi_environment_sha256": sha256_bytes(canonical_json_bytes(PI_RUNTIME_ENVIRONMENT)),
                "pi_runtime_installation": PI_RUNTIME_INSTALLATION,
                "policy": RUNTIME_POLICY,
            },
        ),
    )
    if source.source_sha256 != RUNTIME_CONFIGURATION_SHA256:
        raise ContractError("M4 Runtime authority does not equal the accepted configuration")
    return source


def _task_material(
    task: ControlledTaskAuthority,
    *,
    image: str,
    build_base_image: str,
    profile_source: CanonicalAuthoritySource,
) -> tuple[dict[str, object], DatasetCaseAuthority, dict[str, object]]:
    task_source = _source("task", cast(JsonValue, task.case.model_dump(mode="json")))
    task_authority_source = _source(
        "controlled-task-authority", cast(JsonValue, task.model_dump(mode="json"))
    )
    task_prompt = _source(
        "task-prompt", {"case": task.case.case_id, "text": task.case.input.prompt}
    )
    task_instruction = _source(
        "task-instruction", {"case": task.case.case_id, "text": "Satisfy the frozen tests."}
    )
    test_authority = _source("test-authority", {"task_authority_id": task.authority_id})
    test_plan = _source("test-plan", {"command": list(task.test_command)})
    test_source = _source(
        "test-source",
        cast(
            JsonValue,
            {
                "files": [
                    {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
                    for item in task.test_files
                ]
            },
        ),
    )
    if test_source.source_sha256 != task.test_source_sha256:
        raise ContractError("M4 task Test authority does not equal the verified task")
    case_identity = {
        "case_id": task.case.case_id,
        "case_version": task.case.case_version,
        "case_set": task.case.case_set,
        "sha256": task_source.source_sha256,
    }
    profile_identity = {
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "sha256": profile_source.source_sha256,
    }
    projection = {
        "name": "imported_projection",
        "version": PROJECTION_VERSION,
        "sha256": sha256_bytes(
            canonical_json_bytes({"name": "imported_projection", "version": PROJECTION_VERSION})
        ),
        "digest_kind": "identity",
    }
    scorer = component_identity("scorer", SCORER_VERSION)
    gate = component_identity("gate_policy", GATE_VERSION)
    expected_authority = materialize_expected_evaluation_authority(
        {
            "schema_version": "agent.evaluator.imported-evaluation-authority/v1",
            "profile": profile_identity,
            "case": case_identity,
            "fixtures": [item.model_dump(mode="json") for item in task.case.fixture_references],
            "projection": projection,
            "scorer": scorer.model_dump(mode="json"),
            "case_gate": gate.model_dump(mode="json"),
        }
    )
    expected_policy = materialize_expected_evaluation_policy(
        {
            "schema_version": "agent.evaluator.comparison-evaluation-policy/v1",
            "profile": profile_identity,
            "projection": projection,
            "scorer": scorer.model_dump(mode="json"),
            "case_gate": gate.model_dump(mode="json"),
        }
    )
    dataset_case = DatasetCaseAuthority(
        case=EvaluationCaseIdentitySource.model_validate(case_identity),
        task_source_sha256=task_source.source_sha256,
        task_authority_id=task.authority_id,
        task_authority_sha256=task_authority_source.source_sha256,
        task_prompt_sha256=task_prompt.source_sha256,
        task_instruction_sha256=task_instruction.source_sha256,
        allowed_paths=task.allowed_paths,
        protected_paths=task.protected_paths,
        task_image=image,
        build_base_image=build_base_image,
        test_authority_id="synthetic-python-test-runner",
        test_authority_version="1",
        test_authority_sha256=test_authority.source_sha256,
        test_plan_sha256=test_plan.source_sha256,
        test_source_sha256=test_source.source_sha256,
        test_command=task.test_command,
        test_working_directory="candidate",
        fixtures=task.case.fixture_references,
    )
    task_payload: dict[str, object] = {
        "task_id": task.case.case_id,
        "task_version": task.case.case_version,
        "case_set": task.case.case_set,
        "content_sha256": task_source.source_sha256,
        "task_source": task_source.model_dump(mode="json"),
        "authority_id": task.authority_id,
        "authority_sha256": task_authority_source.source_sha256,
        "authority_source": task_authority_source.model_dump(mode="json"),
        "prompt_sha256": task_prompt.source_sha256,
        "prompt_source": task_prompt.model_dump(mode="json"),
        "instruction_sha256": task_instruction.source_sha256,
        "instruction_source": task_instruction.model_dump(mode="json"),
        "allowed_paths": list(task.allowed_paths),
        "protected_paths": list(task.protected_paths),
    }
    evaluation_payload: dict[str, object] = {
        "test_runner": {
            "authority_id": "synthetic-python-test-runner",
            "authority_version": "1",
            "authority_sha256": test_authority.source_sha256,
            "authority_source": test_authority.model_dump(mode="json"),
            "test_plan_sha256": test_plan.source_sha256,
            "test_plan_source": test_plan.model_dump(mode="json"),
            "test_source_sha256": test_source.source_sha256,
            "test_source": test_source.model_dump(mode="json"),
            "command": list(task.test_command),
            "working_directory": "candidate",
        },
        "expected_evaluation_authority": expected_authority.model_dump(mode="json"),
        "expected_evaluation_policy": expected_policy.model_dump(mode="json"),
    }
    return task_payload, dataset_case, evaluation_payload


def _specification(
    task: ControlledTaskAuthority,
    *,
    configuration_id: str,
    prompt_source: CanonicalAuthoritySource,
    image: str,
    build_base_image: str,
    profile_source: CanonicalAuthoritySource,
    dataset: object,
    statistics: object,
    task_payload: dict[str, object],
    evaluation_payload: dict[str, object],
    timeout_seconds: int = 300,
) -> ControlledExperimentSpecV2:
    runtime_source = _runtime_source()
    harness_source = _source(
        "harness",
        {
            "agent": "cernora_reference_workflow.runtime_agent:TelemetryDisabledPi",
            "environment": "docker",
            "mode": "single-attempt",
            "version": "0.16.1",
        },
    )
    instruction_source = _source(
        "treatment-instruction", {"text": "Change only allowed source files."}
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.controlled-experiment-spec/v2",
        "configuration_id": configuration_id,
        "task": task_payload,
        "container": {
            "image": image,
            "build_base_image": build_base_image,
            "platform": _PLATFORM,
        },
        "harness": {
            "name": "harbor",
            "version": "0.16.1",
            "configuration_sha256": harness_source.source_sha256,
            "configuration_source": harness_source.model_dump(mode="json"),
        },
        "runtime": {
            "name": "pi",
            "version": PI_VERSION,
            "configuration_sha256": runtime_source.source_sha256,
            "configuration_source": runtime_source.model_dump(mode="json"),
            "model": _MODEL,
            "reasoning_effort": _REASONING_EFFORT,
        },
        "prompt_sha256": prompt_source.source_sha256,
        "prompt_source": prompt_source.model_dump(mode="json"),
        "instruction_sha256": instruction_source.source_sha256,
        "instruction_source": instruction_source.model_dump(mode="json"),
        "tool_schema_source": _source(
            "tool-schema", {"environment": "docker", "tools": ["shell"]}
        ).model_dump(mode="json"),
        "generation_configuration_source": _source(
            "generation-configuration",
            {"reasoning_effort": _REASONING_EFFORT, "reasoning_summary": "none"},
        ).model_dump(mode="json"),
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
        **evaluation_payload,
        "profile": {
            "profile_id": PROFILE_ID,
            "profile_version": PROFILE_VERSION,
            "authority_sha256": profile_source.source_sha256,
            "authority_source": profile_source.model_dump(mode="json"),
        },
        "dataset_authority": dataset,
        "statistical_policy": statistics,
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
    return materialize_controlled_experiment_spec(payload)


def build_controlled_specifications(
    *,
    tasks: tuple[ControlledTaskAuthority, ...],
    images: Mapping[str, str],
    build_base_image: str,
    configurations: tuple[tuple[str, CanonicalAuthoritySource], ...],
    bootstrap: BootstrapPlan,
    pass_k: PassKPlan | None,
    timeout_seconds: int = 300,
) -> tuple[ControlledExperimentSpecV2, ...]:
    """Build exact controlled specifications for one closed task/configuration matrix."""

    ordered_tasks = tuple(sorted(tasks, key=lambda item: item.case.case_id))
    case_ids = tuple(item.case.case_id for item in ordered_tasks)
    configuration_ids = tuple(item[0] for item in configurations)
    if (
        not ordered_tasks
        or len(case_ids) != len(set(case_ids))
        or case_ids != tuple(sorted(images))
        or not configurations
        or len(configuration_ids) != len(set(configuration_ids))
    ):
        raise ContractError("controlled specification inputs are incomplete or ambiguous")
    profile = build_controlled_profile_authority(ordered_tasks)
    profile_source = _source(
        "profile", cast(JsonValue, profile.model_dump(mode="json", exclude_none=False))
    )
    materials = {
        task.case.case_id: _task_material(
            task,
            image=images[task.case.case_id],
            build_base_image=build_base_image,
            profile_source=profile_source,
        )
        for task in ordered_tasks
    }
    dataset = materialize_dataset_authority(tuple(materials[case_id][1] for case_id in case_ids))
    statistics = materialize_statistical_policy(bootstrap=bootstrap, pass_k=pass_k)
    return tuple(
        _specification(
            task,
            configuration_id=configuration_id,
            prompt_source=prompt,
            image=images[task.case.case_id],
            build_base_image=build_base_image,
            profile_source=profile_source,
            dataset=dataset.model_dump(mode="json"),
            statistics=statistics.model_dump(mode="json"),
            task_payload=materials[task.case.case_id][0],
            evaluation_payload=materials[task.case.case_id][2],
            timeout_seconds=timeout_seconds,
        )
        for task in ordered_tasks
        for configuration_id, prompt in configurations
    )


def build_m4_final_plans(
    *,
    tasks: tuple[ControlledTaskAuthority, ...],
    freeze: CandidateFreeze,
    image_authorities: M4ImageAuthoritySet,
) -> tuple[ControlledRunPlanV2, ComparisonPlanV1]:
    """Construct the frozen 9x2x3 declaration without consulting test helpers."""

    ordered_tasks = tuple(sorted(tasks, key=lambda item: item.case.case_id))
    case_ids = tuple(item.case.case_id for item in ordered_tasks)
    split_map = {item.case.case_id: item.split_id for item in ordered_tasks}
    if len(ordered_tasks) != 9 or len(set(case_ids)) != 9:
        raise ContractError("M4 final Plan requires exactly nine unique task authorities")
    split_cases = {
        split_id: tuple(sorted(case for case, split in split_map.items() if split == split_id))
        for split_id in set(split_map.values())
    }
    if (
        set(split_cases) != {"development", "regression", "held-out"}
        or any(len(items) != 3 for items in split_cases.values())
        or split_cases["development"] != freeze.pilot.development_case_ids
    ):
        raise ContractError("M4 final Plan requires exact 3/3/3 authoritative splits")
    image_by_case = {item.case_id: item.image for item in image_authorities.images}
    if set(image_by_case) != set(case_ids):
        raise ContractError("M4 image authorities do not exhaust the task authorities")

    specs = build_controlled_specifications(
        tasks=ordered_tasks,
        images=image_by_case,
        build_base_image=image_authorities.build_base_image,
        configurations=(
            (_BASELINE_CONFIGURATION, freeze.baseline_prompt_authority),
            (_CANDIDATE_CONFIGURATION, freeze.candidate_prompt_authority),
        ),
        bootstrap=BootstrapPlan(
            method="case-clustered-paired-bootstrap/v1",
            confidence_basis_points=9500,
            resamples=10000,
            percentile="nearest_rank_closed",
            seed_source="comparison_input_sha256",
        ),
        pass_k=PassKPlan(k=3, independent_trials=True),
    )
    statistics = specs[0].statistical_policy
    plan = materialize_controlled_run_plan(
        {
            "schema_version": "cernora.reference.controlled-run-plan/v2",
            "companion_version": "0.4.0",
            "cernora_version": "0.1.4",
            "connector": {
                "connector_id": "cernora-reference-harbor-pi",
                "connector_version": "2",
                "platform_qualification": "macos-arm64",
            },
            "experiment_specs": [item.model_dump(mode="json") for item in specs],
            "cases": [
                {
                    "case_id": task.case.case_id,
                    "case_version": task.case.case_version,
                    "task_content_sha256": task.case_sha256,
                }
                for task in ordered_tasks
            ],
            "configurations": [
                {"configuration_id": _BASELINE_CONFIGURATION},
                {"configuration_id": _CANDIDATE_CONFIGURATION},
            ],
            "cells": [
                {
                    "case_id": item.task.task_id,
                    "configuration_id": item.configuration_id,
                    "experiment_id": item.experiment_id,
                }
                for item in specs
            ],
            "repetitions": 3,
            "pairing_rule": "case-configuration-repetition",
            "planned_trial_count": 54,
            "worst_case_attempt_count": 108,
            "execution": {
                "concurrency": 1,
                "max_attempt_count": 108,
                "max_total_wall_time_seconds": 43200,
                "token_budget": {
                    "status": "unavailable",
                    "reason": "no-structured-authoritative-source",
                },
                "monetary_budget": {
                    "status": "unavailable",
                    "reason": "no-structured-authoritative-source",
                },
            },
            "analysis": {
                "method": "controlled-comparison",
                "method_version": "m4",
                "aggregate_quality_conclusion": False,
            },
        }
    )
    comparison = materialize_comparison_plan(
        {
            "schema_version": "cernora.reference.comparison-plan/v1",
            "source_run_plan_id": plan.run_plan_id,
            "baseline_configuration_id": _BASELINE_CONFIGURATION,
            "candidate_configuration_id": _CANDIDATE_CONFIGURATION,
            "case_splits": [
                {"case_id": case.case_id, "split_id": split_map[case.case_id]}
                for case in plan.cases
            ],
            "treatment": materialize_treatment_declaration(("prompt_instruction",)).model_dump(
                mode="json"
            ),
            "primary_outcome": {
                "metric": "reliable_success_rate",
                "scope": "split",
                "split_id": "held-out",
                "direction": "higher_is_better",
                "practical_threshold_basis_points": 1000,
            },
            "guardrails": [
                {
                    "guardrail_id": "evaluation-validity",
                    "hard": True,
                    "metric": "evaluation_validity_rate",
                    "scope": "all",
                    "split_id": None,
                    "direction": "higher_is_better",
                    "max_adverse_basis_points": 0,
                    "profile_id": None,
                    "profile_version": None,
                    "failure_code": None,
                },
                {
                    "guardrail_id": "protected-paths",
                    "hard": True,
                    "metric": "profile_failure_code_rate",
                    "scope": "all",
                    "split_id": None,
                    "direction": "lower_is_better",
                    "max_adverse_basis_points": 0,
                    "profile_id": PROFILE_ID,
                    "profile_version": PROFILE_VERSION,
                    "failure_code": "protected_paths_unchanged_v1",
                },
                {
                    "guardrail_id": "regression-rsr",
                    "hard": True,
                    "metric": "reliable_success_rate",
                    "scope": "split",
                    "split_id": "regression",
                    "direction": "higher_is_better",
                    "max_adverse_basis_points": 1000,
                    "profile_id": None,
                    "profile_version": None,
                    "failure_code": None,
                },
            ],
            "bootstrap": statistics.bootstrap.model_dump(mode="json"),
            "pass_k": statistics.pass_k.model_dump(mode="json") if statistics.pass_k else None,
            "statistical_policy": statistics.model_dump(mode="json"),
        }
    )
    comparison.validate_run_plan(plan)
    if len({item.trial_slot_id for item in plan.expand_trial_slots()}) != 54:
        raise ContractError("M4 final Plan does not expand to 54 unique Trial slots")
    return plan, comparison


__all__ = [
    "M4ImageAuthoritySet",
    "M4TaskImageAuthority",
    "build_controlled_specifications",
    "build_m4_final_plans",
    "materialize_m4_image_authority_set",
]
