"""Strict controlled ExperimentSpec v2 and Core authority projection."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from cernora import (
    BootstrapPlan,
    ComponentIdentity,
    ExperimentAuthority,
    ExperimentProjection,
    FixtureReference,
    PassKPlan,
    component_identity,
    materialize_experiment_authority,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
    validate_relative_path,
    validate_sha256,
)

ACCEPTED_CORE_0_1_4_WHEEL_SHA256 = (
    "5b847837b7182b3ece8054eb5187fde4f835582787b406ea4a7f2f8bd2987a4c"
)
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
NonEmpty = Annotated[StrictStr, Field(min_length=1)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]


class StrictV2Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _digest(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))


class CanonicalAuthoritySource(StrictV2Contract):
    """One embedded source document whose digest is never caller-asserted."""

    schema_version: Literal["cernora.reference.canonical-authority-source/v1"]
    source_id: Identifier
    source_sha256: Digest
    payload: JsonValue

    @model_validator(mode="after")
    def rederive_digest(self) -> Self:
        if self.source_sha256 != _digest(self.payload):
            raise ValueError("authority source digest does not match canonical payload")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def materialize_authority_source(source_id: str, payload: JsonValue) -> CanonicalAuthoritySource:
    return CanonicalAuthoritySource(
        schema_version="cernora.reference.canonical-authority-source/v1",
        source_id=source_id,
        source_sha256=_digest(payload),
        payload=payload,
    )


class ControlledTaskContract(StrictV2Contract):
    task_id: Identifier
    task_version: NonEmpty
    case_set: NonEmpty
    content_sha256: Digest
    task_source: CanonicalAuthoritySource
    authority_id: Digest
    authority_sha256: Digest
    authority_source: CanonicalAuthoritySource
    prompt_sha256: Digest
    prompt_source: CanonicalAuthoritySource
    instruction_sha256: Digest
    instruction_source: CanonicalAuthoritySource
    allowed_paths: tuple[NonEmpty, ...]
    protected_paths: tuple[NonEmpty, ...]

    @field_validator("allowed_paths", "protected_paths", mode="before")
    @classmethod
    def tuple_paths(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_sources_and_paths(self) -> Self:
        if self.content_sha256 != self.task_source.source_sha256:
            raise ValueError("task content digest does not match its canonical source")
        authority_payload = self.authority_source.payload
        if (
            self.authority_sha256 != self.authority_source.source_sha256
            or not isinstance(authority_payload, dict)
            or authority_payload.get("authority_id") != self.authority_id
        ):
            raise ValueError("controlled task authority is not bound to its canonical source")
        if self.prompt_sha256 != self.prompt_source.source_sha256 or (
            self.instruction_sha256 != self.instruction_source.source_sha256
        ):
            raise ValueError("task prompt/instruction digest does not match its canonical source")
        allowed = tuple(validate_relative_path(path) for path in self.allowed_paths)
        protected = tuple(validate_relative_path(path) for path in self.protected_paths)
        if not allowed or not protected:
            raise ValueError("allowed_paths and protected_paths must be non-empty")
        if allowed != tuple(sorted(allowed)) or protected != tuple(sorted(protected)):
            raise ValueError("task path policies must be sorted")
        if len(set(allowed + protected)) != len(allowed + protected):
            raise ValueError("task path policies must be unique and disjoint")
        return self


class ControlledContainerContract(StrictV2Contract):
    image: NonEmpty
    build_base_image: NonEmpty
    platform: NonEmpty

    @model_validator(mode="after")
    def require_immutable_digests(self) -> Self:
        marker = "@sha256:"
        for label, image in (
            ("task image", self.image),
            ("build base image", self.build_base_image),
        ):
            if marker not in image:
                raise ValueError(f"{label} must include an immutable sha256 digest")
            validate_sha256(image.rsplit(marker, 1)[1], label=f"{label} digest")
        return self


class ControlledHarnessContract(StrictV2Contract):
    name: NonEmpty
    version: NonEmpty
    configuration_sha256: Digest
    configuration_source: CanonicalAuthoritySource

    @model_validator(mode="after")
    def bind_configuration(self) -> Self:
        if self.configuration_sha256 != self.configuration_source.source_sha256:
            raise ValueError("Harness configuration digest does not match its canonical source")
        return self


class ControlledRuntimeContract(StrictV2Contract):
    name: NonEmpty
    version: NonEmpty
    configuration_sha256: Digest
    configuration_source: CanonicalAuthoritySource
    model: NonEmpty
    reasoning_effort: NonEmpty

    @model_validator(mode="after")
    def bind_configuration(self) -> Self:
        if self.configuration_sha256 != self.configuration_source.source_sha256:
            raise ValueError("Runtime configuration digest does not match its canonical source")
        return self


class ControlledLimits(StrictV2Contract):
    agent_setup_timeout_seconds: PositiveInt
    timeout_seconds: PositiveInt
    memory_mebibytes: PositiveInt
    cpu_millis: PositiveInt


class ControlledNetworkPolicy(StrictV2Contract):
    provider_egress: Literal["required-allowed"]
    web_search: Annotated[StrictBool, Field(strict=True)]

    @model_validator(mode="after")
    def require_web_search_disabled(self) -> Self:
        if self.web_search:
            raise ValueError("web search must be disabled")
        return self


class ControlledRetryPolicy(StrictV2Contract):
    max_retries: Literal[1]
    delay_seconds: Literal[10]
    jitter: Annotated[StrictBool, Field(strict=True)]
    eligible_states: tuple[
        Literal["infrastructure-start-failure", "transient-provider-pre-terminal"], ...
    ]

    @field_validator("eligible_states", mode="before")
    @classmethod
    def tuple_states(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_retry_scope(self) -> Self:
        required = (
            "infrastructure-start-failure",
            "transient-provider-pre-terminal",
        )
        if self.jitter or self.eligible_states != required:
            raise ValueError("retry policy must equal the approved ordered policy")
        return self


class ControlledTestAuthority(StrictV2Contract):
    authority_id: Identifier
    authority_version: NonEmpty
    authority_sha256: Digest
    authority_source: CanonicalAuthoritySource
    test_plan_sha256: Digest
    test_plan_source: CanonicalAuthoritySource
    test_source_sha256: Digest
    test_source: CanonicalAuthoritySource
    command: tuple[NonEmpty, ...]
    working_directory: Literal["candidate"]

    @field_validator("command", mode="before")
    @classmethod
    def tuple_command(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def bind_sources(self) -> Self:
        bindings = (
            (self.authority_sha256, self.authority_source.source_sha256),
            (self.test_plan_sha256, self.test_plan_source.source_sha256),
            (self.test_source_sha256, self.test_source.source_sha256),
        )
        if any(actual != expected for actual, expected in bindings):
            raise ValueError("Test authority digest does not match its canonical source")
        if not self.command:
            raise ValueError("test command must be non-empty")
        return self


class ControlledProfileAuthority(StrictV2Contract):
    profile_id: Identifier
    profile_version: NonEmpty
    authority_sha256: Digest
    authority_source: CanonicalAuthoritySource

    @model_validator(mode="after")
    def bind_source(self) -> Self:
        if self.authority_sha256 != self.authority_source.source_sha256:
            raise ValueError("Profile authority digest does not match its canonical source")
        return self


class ControlledWorkflowVersions(StrictV2Contract):
    exporter: NonEmpty
    adapter: NonEmpty
    report: NonEmpty


class ControlledCernoraRelease(StrictV2Contract):
    package_version: Literal["0.1.4"]
    wheel_sha256: Literal["5b847837b7182b3ece8054eb5187fde4f835582787b406ea4a7f2f8bd2987a4c"]


class EvaluationProfileIdentitySource(StrictV2Contract):
    profile_id: Identifier
    profile_version: NonEmpty
    sha256: Digest


class EvaluationCaseIdentitySource(StrictV2Contract):
    case_id: Identifier
    case_version: NonEmpty
    case_set: NonEmpty
    sha256: Digest


class ImportedProjectionIdentitySource(StrictV2Contract):
    name: Literal["imported_projection"]
    version: NonEmpty
    sha256: Digest
    digest_kind: Literal["identity"] = "identity"

    @model_validator(mode="after")
    def rederive_identity(self) -> Self:
        expected = _digest({"name": self.name, "version": self.version})
        if self.sha256 != expected:
            raise ValueError("imported projection identity does not match semantic content")
        return self


class ExpectedEvaluationAuthoritySource(StrictV2Contract):
    """Precomputable canonical ImportedEvaluationAuthority payload."""

    schema_version: Literal["agent.evaluator.imported-evaluation-authority/v1"]
    profile: EvaluationProfileIdentitySource
    case: EvaluationCaseIdentitySource
    fixtures: tuple[FixtureReference, ...] = Field(min_length=1)
    projection: ImportedProjectionIdentitySource
    scorer: ComponentIdentity
    case_gate: ComponentIdentity
    authority_id: NonEmpty
    authority_sha256: Digest

    @field_validator("fixtures", mode="before")
    @classmethod
    def tuple_fixtures(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def rederive_authority(self) -> Self:
        fixtures = tuple((item.fixture_id, item.path) for item in self.fixtures)
        if fixtures != tuple(sorted(fixtures)) or len(fixtures) != len(set(fixtures)):
            raise ValueError("evaluation fixtures must be sorted and unique")
        if len({item.fixture_id for item in self.fixtures}) != len(self.fixtures):
            raise ValueError("evaluation fixture IDs must be unique")
        if len({item.path for item in self.fixtures}) != len(self.fixtures):
            raise ValueError("evaluation fixture paths must be unique")
        if self.scorer != component_identity("scorer", self.scorer.version) or (
            self.case_gate != component_identity("gate_policy", self.case_gate.version)
        ):
            raise ValueError("evaluation component identity is not canonical")
        payload = self.model_dump(mode="json", exclude={"authority_id", "authority_sha256"})
        digest = _digest(payload)
        if self.authority_sha256 != digest or self.authority_id != f"imported-authority-{digest}":
            raise ValueError("expected evaluation authority identity is not canonical")
        return self


def materialize_expected_evaluation_authority(
    payload_without_identity: Mapping[str, object],
) -> ExpectedEvaluationAuthoritySource:
    if {"authority_id", "authority_sha256"}.intersection(payload_without_identity):
        raise ContractError("evaluation authority materialization input must omit identity")
    payload = dict(payload_without_identity)
    digest = _digest(payload)
    payload["authority_id"] = f"imported-authority-{digest}"
    payload["authority_sha256"] = digest
    return ExpectedEvaluationAuthoritySource.model_validate(payload)


class ExpectedEvaluationPolicySource(StrictV2Contract):
    """The exact case-neutral source consumed by Core evaluation-policy binding."""

    schema_version: Literal["agent.evaluator.comparison-evaluation-policy/v1"]
    profile: EvaluationProfileIdentitySource
    projection: ImportedProjectionIdentitySource
    scorer: ComponentIdentity
    case_gate: ComponentIdentity
    policy_sha256: Digest

    @model_validator(mode="after")
    def rederive_policy(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"policy_sha256"})
        if self.policy_sha256 != _digest(payload):
            raise ValueError("expected evaluation policy digest is not canonical")
        return self


def materialize_expected_evaluation_policy(
    payload_without_digest: Mapping[str, object],
) -> ExpectedEvaluationPolicySource:
    if "policy_sha256" in payload_without_digest:
        raise ContractError("evaluation policy materialization input must omit policy_sha256")
    payload = dict(payload_without_digest)
    payload["policy_sha256"] = _digest(payload)
    return ExpectedEvaluationPolicySource.model_validate(payload)


class StatisticalPolicy(StrictV2Contract):
    """Independent comparison policy, intentionally free of Experiment/Plan IDs."""

    schema_version: Literal["cernora.reference.statistical-policy/v1"]
    statistical_policy_id: NonEmpty
    statistical_policy_sha256: Digest
    bootstrap: BootstrapPlan
    pass_k: PassKPlan | None

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        payload = self.model_dump(
            mode="json", exclude={"statistical_policy_id", "statistical_policy_sha256"}
        )
        digest = _digest(payload)
        if (
            self.statistical_policy_sha256 != digest
            or self.statistical_policy_id != f"statistical-policy-{digest}"
        ):
            raise ValueError("statistical policy identity is not canonical")
        return self


def materialize_statistical_policy(
    *, bootstrap: BootstrapPlan, pass_k: PassKPlan | None
) -> StatisticalPolicy:
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.statistical-policy/v1",
        "bootstrap": bootstrap.model_dump(mode="json"),
        "pass_k": None if pass_k is None else pass_k.model_dump(mode="json"),
    }
    digest = _digest(payload)
    payload["statistical_policy_id"] = f"statistical-policy-{digest}"
    payload["statistical_policy_sha256"] = digest
    return StatisticalPolicy.model_validate(payload)


class DatasetCaseAuthority(StrictV2Contract):
    case: EvaluationCaseIdentitySource
    task_source_sha256: Digest
    task_authority_id: Digest
    task_authority_sha256: Digest
    task_prompt_sha256: Digest
    task_instruction_sha256: Digest
    allowed_paths: tuple[NonEmpty, ...]
    protected_paths: tuple[NonEmpty, ...]
    task_image: NonEmpty
    build_base_image: NonEmpty
    test_authority_id: Identifier
    test_authority_version: NonEmpty
    test_authority_sha256: Digest
    test_plan_sha256: Digest
    test_source_sha256: Digest
    test_command: tuple[NonEmpty, ...]
    test_working_directory: Literal["candidate"]
    fixtures: tuple[FixtureReference, ...] = Field(min_length=1)

    @field_validator("allowed_paths", "protected_paths", "test_command", "fixtures", mode="before")
    @classmethod
    def tuple_collections(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def sorted_fixtures(self) -> Self:
        coordinates = tuple((item.fixture_id, item.path) for item in self.fixtures)
        if coordinates != tuple(sorted(coordinates)) or len(coordinates) != len(set(coordinates)):
            raise ValueError("Dataset Case fixtures must be sorted and unique")
        if len({item.fixture_id for item in self.fixtures}) != len(self.fixtures):
            raise ValueError("Dataset Case fixture IDs must be unique")
        if len({item.path for item in self.fixtures}) != len(self.fixtures):
            raise ValueError("Dataset Case fixture paths must be unique")
        return self


class DatasetAuthority(StrictV2Contract):
    """Configuration-neutral closed manifest for every selected Case."""

    schema_version: Literal["cernora.reference.dataset-authority/v1"]
    dataset_id: NonEmpty
    dataset_sha256: Digest
    cases: tuple[DatasetCaseAuthority, ...] = Field(min_length=1)

    @field_validator("cases", mode="before")
    @classmethod
    def tuple_cases(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        case_ids = tuple(item.case.case_id for item in self.cases)
        if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("Dataset authority Cases must be sorted and unique")
        payload = self.model_dump(mode="json", exclude={"dataset_id", "dataset_sha256"})
        digest = _digest(payload)
        if self.dataset_sha256 != digest or self.dataset_id != f"dataset-{digest}":
            raise ValueError("Dataset authority identity is not canonical")
        return self


def materialize_dataset_authority(
    cases: tuple[DatasetCaseAuthority, ...],
) -> DatasetAuthority:
    ordered = tuple(sorted(cases, key=lambda item: item.case.case_id))
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.dataset-authority/v1",
        "cases": [item.model_dump(mode="json") for item in ordered],
    }
    digest = _digest(payload)
    payload["dataset_id"] = f"dataset-{digest}"
    payload["dataset_sha256"] = digest
    return DatasetAuthority.model_validate(payload)


class ControlledExperimentSpecSource(StrictV2Contract):
    schema_version: Literal["cernora.reference.controlled-experiment-spec/v2"]
    configuration_id: Identifier
    task: ControlledTaskContract
    container: ControlledContainerContract
    harness: ControlledHarnessContract
    runtime: ControlledRuntimeContract
    prompt_sha256: Digest
    prompt_source: CanonicalAuthoritySource
    instruction_sha256: Digest
    instruction_source: CanonicalAuthoritySource
    tool_schema_source: CanonicalAuthoritySource
    generation_configuration_source: CanonicalAuthoritySource
    limits: ControlledLimits
    network: ControlledNetworkPolicy
    retry: ControlledRetryPolicy
    test_runner: ControlledTestAuthority
    profile: ControlledProfileAuthority
    expected_evaluation_authority: ExpectedEvaluationAuthoritySource
    expected_evaluation_policy: ExpectedEvaluationPolicySource
    dataset_authority: DatasetAuthority
    statistical_policy: StatisticalPolicy
    workflow: ControlledWorkflowVersions
    cernora: ControlledCernoraRelease

    @model_validator(mode="after")
    def validate_source_bindings(self) -> Self:
        if self.prompt_sha256 != self.prompt_source.source_sha256 or (
            self.instruction_sha256 != self.instruction_source.source_sha256
        ):
            raise ValueError("Treatment prompt/instruction is not bound to its canonical source")

        authority = self.expected_evaluation_authority
        policy = self.expected_evaluation_policy
        if authority.case.model_dump(mode="json") != self.case_identity():
            raise ValueError("expected evaluation authority does not bind the task Case")
        if authority.profile.profile_id != self.profile.profile_id or (
            authority.profile.profile_version != self.profile.profile_version
            or authority.profile.sha256 != self.profile.authority_sha256
        ):
            raise ValueError("expected evaluation authority does not bind the Profile")
        if (
            policy.profile != authority.profile
            or policy.projection != authority.projection
            or policy.scorer != authority.scorer
            or policy.case_gate != authority.case_gate
        ):
            raise ValueError("expected evaluation policy does not bind the authority components")
        dataset_cases = {item.case.case_id: item for item in self.dataset_authority.cases}
        dataset_case = dataset_cases.get(self.task.task_id)
        if dataset_case is None:
            raise ValueError("Experiment Case is absent from the Dataset authority")
        if (
            dataset_case.case.model_dump(mode="json") != self.case_identity()
            or dataset_case.task_source_sha256 != self.task.task_source.source_sha256
            or dataset_case.task_authority_id != self.task.authority_id
            or dataset_case.task_authority_sha256 != self.task.authority_sha256
            or dataset_case.task_prompt_sha256 != self.task.prompt_sha256
            or dataset_case.task_instruction_sha256 != self.task.instruction_sha256
            or dataset_case.allowed_paths != self.task.allowed_paths
            or dataset_case.protected_paths != self.task.protected_paths
            or dataset_case.task_image != self.container.image
            or dataset_case.build_base_image != self.container.build_base_image
            or dataset_case.test_authority_id != self.test_runner.authority_id
            or dataset_case.test_authority_version != self.test_runner.authority_version
            or dataset_case.test_authority_sha256 != self.test_runner.authority_sha256
            or dataset_case.test_plan_sha256 != self.test_runner.test_plan_sha256
            or dataset_case.test_source_sha256 != self.test_runner.test_source_sha256
            or dataset_case.test_command != self.test_runner.command
            or dataset_case.test_working_directory != self.test_runner.working_directory
            or dataset_case.fixtures != authority.fixtures
        ):
            raise ValueError("Experiment Case material does not match the Dataset authority")
        return self

    def case_identity(self) -> dict[str, str]:
        return {
            "case_id": self.task.task_id,
            "case_version": self.task.task_version,
            "case_set": self.task.case_set,
            "sha256": self.task.content_sha256,
        }

    def _projection_digest(self, field: str, source: object) -> str:
        return _digest(
            {
                "schema_version": "cernora.reference.experiment-projection-source/v1",
                "field": field,
                "source": source,
            }
        )

    def core_projection(self) -> ExperimentProjection:
        runtime = self.runtime.model_dump(mode="json")
        harness = self.harness.model_dump(mode="json")
        network = self.network.model_dump(mode="json")
        return ExperimentProjection(
            runtime_version_sha256=self._projection_digest(
                "runtime_version", {"runtime": runtime, "harness": harness}
            ),
            model_sha256=self._projection_digest(
                "model",
                {
                    "model": self.runtime.model,
                    "reasoning_effort": self.runtime.reasoning_effort,
                    "runtime_configuration": self.runtime.configuration_source.model_dump(
                        mode="json"
                    ),
                },
            ),
            prompt_instruction_sha256=self._projection_digest(
                "prompt_instruction",
                {
                    "prompt": self.prompt_source.model_dump(mode="json"),
                    "instruction": self.instruction_source.model_dump(mode="json"),
                },
            ),
            tool_schema_sha256=self._projection_digest(
                "tool_schema",
                {
                    "tool_schema": self.tool_schema_source.model_dump(mode="json"),
                    "runtime_configuration": self.runtime.configuration_source.model_dump(
                        mode="json"
                    ),
                    "network": network,
                },
            ),
            generation_configuration_sha256=self._projection_digest(
                "generation_configuration",
                {
                    "generation_configuration": self.generation_configuration_source.model_dump(
                        mode="json"
                    ),
                    "runtime_configuration": self.runtime.configuration_source.model_dump(
                        mode="json"
                    ),
                },
            ),
            timeout_sha256=self._projection_digest(
                "timeout",
                {
                    "agent_setup_timeout_seconds": self.limits.agent_setup_timeout_seconds,
                    "timeout_seconds": self.limits.timeout_seconds,
                },
            ),
            resources_sha256=self._projection_digest(
                "resources",
                {
                    "platform": self.container.platform,
                    "memory_mebibytes": self.limits.memory_mebibytes,
                    "cpu_millis": self.limits.cpu_millis,
                },
            ),
            retry_policy_sha256=self._projection_digest(
                "retry_policy", self.retry.model_dump(mode="json")
            ),
            dataset_sha256=self.dataset_authority.dataset_sha256,
            profile_sha256=self.profile.authority_sha256,
            evaluation_authority_sha256=(self.expected_evaluation_authority.authority_sha256),
            evaluation_policy_sha256=self.expected_evaluation_policy.policy_sha256,
            report_contract_sha256=self._projection_digest(
                "report_contract",
                {
                    "workflow": self.workflow.model_dump(mode="json"),
                    "cernora": self.cernora.model_dump(mode="json"),
                },
            ),
            statistical_plan_sha256=self.statistical_policy.statistical_policy_sha256,
        )

    def core_authority(self) -> ExperimentAuthority:
        return materialize_experiment_authority(
            {
                "schema_version": "agent.evaluator.comparison-experiment-authority/v1",
                "configuration_id": self.configuration_id,
                "case": self.case_identity(),
                "projection": self.core_projection().model_dump(mode="json"),
            }
        )


class ControlledExperimentSpecV2(ControlledExperimentSpecSource):
    experiment_id: Digest

    @model_validator(mode="after")
    def bind_core_authority(self) -> Self:
        if self.experiment_id != self.core_authority().experiment_id:
            raise ValueError("experiment_id does not match the Core Experiment authority")
        return self

    @classmethod
    def from_file(cls, path: Path) -> ControlledExperimentSpecV2:
        data = read_regular_file_bytes(path)
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise ContractError("ControlledExperimentSpecV2 must be a JSON object")
        spec = cls.model_validate(payload)
        if data != spec.canonical_bytes():
            raise ContractError("ControlledExperimentSpecV2 is not canonical JSON")
        return spec

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def materialize_controlled_experiment_spec(
    payload_without_id: Mapping[str, object],
) -> ControlledExperimentSpecV2:
    if "experiment_id" in payload_without_id:
        raise ContractError("ControlledExperimentSpecV2 materialization input must omit identity")
    source = ControlledExperimentSpecSource.model_validate(payload_without_id)
    payload = source.model_dump(mode="json")
    payload["experiment_id"] = source.core_authority().experiment_id
    return ControlledExperimentSpecV2.model_validate(payload)


__all__ = [
    "ACCEPTED_CORE_0_1_4_WHEEL_SHA256",
    "CanonicalAuthoritySource",
    "ControlledCernoraRelease",
    "ControlledContainerContract",
    "ControlledExperimentSpecV2",
    "ControlledHarnessContract",
    "ControlledLimits",
    "ControlledNetworkPolicy",
    "ControlledProfileAuthority",
    "ControlledRetryPolicy",
    "ControlledRuntimeContract",
    "ControlledTaskContract",
    "ControlledTestAuthority",
    "ControlledWorkflowVersions",
    "DatasetAuthority",
    "DatasetCaseAuthority",
    "EvaluationCaseIdentitySource",
    "EvaluationProfileIdentitySource",
    "ExpectedEvaluationAuthoritySource",
    "ExpectedEvaluationPolicySource",
    "ImportedProjectionIdentitySource",
    "StatisticalPolicy",
    "materialize_authority_source",
    "materialize_controlled_experiment_spec",
    "materialize_dataset_authority",
    "materialize_expected_evaluation_authority",
    "materialize_expected_evaluation_policy",
    "materialize_statistical_policy",
]
