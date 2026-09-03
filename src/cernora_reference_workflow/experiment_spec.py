"""Strict ExperimentSpec v1 model and canonical content identity."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    load_json_file,
    validate_relative_path,
    validate_sha256,
)

Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmpty = Annotated[StrictStr, Field(min_length=1)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskContract(StrictContract):
    task_id: Literal["tiny-calculator-v1", "tiny-calculator-v2"]
    task_version: Literal["1", "2"]
    content_sha256: Digest
    prompt_sha256: Digest
    instruction_sha256: Digest
    allowed_paths: tuple[NonEmpty, ...]
    protected_paths: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def validate_paths(self) -> TaskContract:
        expected_version = {
            "tiny-calculator-v1": "1",
            "tiny-calculator-v2": "2",
        }[self.task_id]
        if self.task_version != expected_version:
            raise ValueError("task ID and version do not match")
        allowed = tuple(validate_relative_path(path) for path in self.allowed_paths)
        protected = tuple(validate_relative_path(path) for path in self.protected_paths)
        if not allowed or not protected:
            raise ValueError("allowed_paths and protected_paths must be non-empty")
        if len(set(allowed + protected)) != len(allowed + protected):
            raise ValueError("task path policy entries must be unique and disjoint")
        return self


class ContainerContract(StrictContract):
    image: NonEmpty
    build_base_image: NonEmpty
    platform: Literal["linux/arm64"]

    @model_validator(mode="after")
    def require_immutable_digest(self) -> ContainerContract:
        marker = "@sha256:"
        for label, image in (
            ("task image", self.image),
            ("build base image", self.build_base_image),
        ):
            if marker not in image:
                raise ValueError(f"{label} must include an immutable sha256 digest")
            validate_sha256(image.rsplit(marker, 1)[1], label=f"{label} digest")
        return self


class ComponentPin(StrictContract):
    name: NonEmpty
    version: NonEmpty
    configuration_sha256: Digest


class RuntimeContract(ComponentPin):
    name: Literal["pi"]
    version: Literal["0.84.4"]
    model: Literal["deepseek/deepseek-v4-flash"]
    reasoning_effort: Literal["medium"]


class HarnessContract(ComponentPin):
    name: Literal["harbor"]
    version: Literal["0.16.1"]


class Limits(StrictContract):
    agent_setup_timeout_seconds: PositiveInt
    timeout_seconds: PositiveInt
    memory_mebibytes: PositiveInt
    cpu_millis: PositiveInt


class NetworkPolicy(StrictContract):
    provider_egress: Literal["required-allowed"]
    web_search: Annotated[StrictBool, Field(strict=True)]

    @model_validator(mode="after")
    def require_web_search_disabled(self) -> NetworkPolicy:
        if self.web_search:
            raise ValueError("web search must be disabled")
        return self


class RetryPolicy(StrictContract):
    max_retries: Literal[1]
    delay_seconds: Literal[10]
    jitter: Annotated[StrictBool, Field(strict=True)]
    eligible_states: tuple[
        Literal["infrastructure-start-failure", "transient-provider-pre-terminal"], ...
    ]

    @model_validator(mode="after")
    def validate_eligible_states(self) -> RetryPolicy:
        if self.jitter:
            raise ValueError("retry jitter must be disabled")
        required = {
            "infrastructure-start-failure",
            "transient-provider-pre-terminal",
        }
        if set(self.eligible_states) != required or len(self.eligible_states) != len(required):
            raise ValueError("retry eligible_states must contain the exact approved set")
        return self


class TestAuthority(StrictContract):
    authority_id: Literal["tiny-calculator-test-runner", "tiny-calculator-v2-test-runner"]
    authority_version: Literal["1"]
    authority_sha256: Digest
    test_plan_sha256: Digest
    test_source_sha256: Digest
    command: tuple[NonEmpty, ...]
    working_directory: Literal["candidate"]


class ProfileAuthority(StrictContract):
    profile_id: Literal["cernora-reference-coding-v1"]
    profile_version: Literal["1.0.0"]
    authority_sha256: Digest


class WorkflowVersions(StrictContract):
    exporter: Literal["completed-export/v1"]
    adapter: Literal["cernora-reference-adapter/v1"]
    report: Literal["cernora-reference-run-report/v1"]


class CernoraRelease(StrictContract):
    package_version: Literal["0.1.2"]
    wheel_sha256: Digest


class ExperimentSpec(StrictContract):
    schema_version: Literal["cernora.reference.experiment-spec/v1"]
    experiment_id: Digest
    task: TaskContract
    container: ContainerContract
    harness: HarnessContract
    runtime: RuntimeContract
    prompt_sha256: Digest
    instruction_sha256: Digest
    limits: Limits
    network: NetworkPolicy
    retry: RetryPolicy
    test_runner: TestAuthority
    profile: ProfileAuthority
    workflow: WorkflowVersions
    cernora: CernoraRelease

    @model_validator(mode="after")
    def validate_identity_and_bindings(self) -> ExperimentSpec:
        if self.prompt_sha256 != self.task.prompt_sha256:
            raise ValueError("top-level prompt digest does not match task binding")
        if self.instruction_sha256 != self.task.instruction_sha256:
            raise ValueError("top-level instruction digest does not match task binding")
        expected = self.compute_experiment_id(self.model_dump(mode="json"))
        if self.experiment_id != expected:
            raise ValueError("experiment_id does not match canonical spec content")
        return self

    @staticmethod
    def compute_experiment_id(payload: dict[str, object]) -> str:
        return canonical_content_id(payload, excluded=frozenset({"experiment_id"}))

    @classmethod
    def from_file(cls, path: Path) -> ExperimentSpec:
        payload = load_json_file(path)
        if not isinstance(payload, dict):
            raise ContractError("ExperimentSpec must be a JSON object")
        spec = cls.model_validate(payload)
        if path.read_bytes() != spec.canonical_bytes():
            raise ContractError("ExperimentSpec is not canonical JSON")
        return spec

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def materialize_experiment_spec(payload_without_id: dict[str, object]) -> ExperimentSpec:
    if "experiment_id" in payload_without_id:
        raise ContractError("materialization input must omit experiment_id")
    payload = dict(payload_without_id)
    payload["experiment_id"] = ExperimentSpec.compute_experiment_id(payload)
    return ExperimentSpec.model_validate(payload)
