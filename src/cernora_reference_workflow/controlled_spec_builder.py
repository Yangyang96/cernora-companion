"""Build authority-bound controlled specifications and task image identities."""

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
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.runtime_policy import (
    PI_RUNTIME_ENVIRONMENT,
    PI_RUNTIME_INSTALLATION,
    PI_VERSION,
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
)

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
