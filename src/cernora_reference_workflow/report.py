"""Strict, portable, machine-authoritative run-report/v1 contract."""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    load_json_file,
    validate_relative_path,
)
from cernora_reference_workflow.experiment_spec import Digest, NonEmpty, StrictContract
from cernora_reference_workflow.lifecycle import TerminalState
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.secrets import PATTERNS, require_secret_free

REPORT_SCHEMA_VERSION = "cernora.reference.run-report/v1"

Identifier = Annotated[
    StrictStr,
    Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$"),
]
ReasonCode = Annotated[
    StrictStr,
    Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9._-]*$"),
]
MissingReason = Literal[
    "not-collected",
    "not-emitted",
    "not-verifiable",
    "not-applicable",
    "attempt-not-exported",
    "evaluation-not-performed",
]

_HOST_PATH = re.compile(
    r"(?:^|[\s\"'=])(?:/Users/|/home/|/root/|/private/(?:tmp|var)/|~/|[A-Za-z]:[\\/])"
)
_ENV_ASSIGNMENT = re.compile(r"(?:^|\s)[A-Z][A-Z0-9_]{2,}=\S+")
_ACCOUNT_VALUE = re.compile(r"(?i)\b(?:account|organization|subscription)[_-]?id\s*[:=]\s*\S+")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


class ReportError(ContractError):
    """A run report is malformed, non-canonical, or non-portable."""


class MissingData(StrictContract):
    status: Literal["missing"]
    reason: MissingReason


class AvailableDigest(StrictContract):
    status: Literal["available"]
    sha256: Digest


DigestOrMissing = Annotated[AvailableDigest | MissingData, Field(discriminator="status")]


class AvailableMetric(StrictContract):
    status: Literal["available"]
    value: Annotated[StrictInt, Field(ge=0)]
    unit: Literal["milliseconds", "bytes", "tokens"]
    source_receipt_sha256: Digest


MetricOrMissing = Annotated[AvailableMetric | MissingData, Field(discriminator="status")]


class NetworkCommandObservation(StrictContract):
    command: Literal["curl", "wget"]
    occurrences: Annotated[StrictInt, Field(gt=0)]


class NetworkCommandsAvailable(StrictContract):
    status: Literal["available"]
    source_receipt_sha256: Digest
    observations: tuple[NetworkCommandObservation, ...]


NetworkCommandsOrMissing = Annotated[
    NetworkCommandsAvailable | MissingData,
    Field(discriminator="status"),
]


class TaskIdentity(StrictContract):
    task_id: Literal["tiny-calculator-v1", "tiny-calculator-v2"]
    task_version: Literal["1", "2"]
    content_sha256: Digest
    prompt_sha256: Digest
    instruction_sha256: Digest

    @model_validator(mode="after")
    def validate_version(self) -> TaskIdentity:
        expected = {"tiny-calculator-v1": "1", "tiny-calculator-v2": "2"}[self.task_id]
        if self.task_version != expected:
            raise ValueError("task ID and version do not match")
        return self


class ContainerIdentity(StrictContract):
    image: NonEmpty
    build_base_image: NonEmpty
    platform: Literal["linux/arm64"]

    @model_validator(mode="after")
    def require_digest(self) -> ContainerIdentity:
        for label, image in (
            ("task image", self.image),
            ("build base image", self.build_base_image),
        ):
            if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
                raise ValueError(f"{label} must contain one immutable sha256 digest")
        return self


class ComponentIdentity(StrictContract):
    name: Identifier
    version: NonEmpty
    configuration_sha256: Digest


class RuntimeIdentity(ComponentIdentity):
    name: Literal["pi"]
    version: Literal["0.84.4"]


class HarnessIdentity(ComponentIdentity):
    name: Literal["harbor"]
    version: Literal["0.16.1"]


class ModelIdentity(StrictContract):
    name: Literal["deepseek/deepseek-v4-flash"]
    reasoning_effort: Literal["medium"]
    web_search: StrictBool
    provider_egress: Literal["required-allowed"]

    @model_validator(mode="after")
    def require_web_search_disabled(self) -> ModelIdentity:
        if self.web_search:
            raise ValueError("web search must be disabled")
        return self


class CernoraIdentity(StrictContract):
    package_version: Literal["0.1.2"]
    wheel_sha256: Digest


class ProfileIdentity(StrictContract):
    profile_id: Literal["cernora-reference-coding-v1"]
    profile_version: Literal["1.0.0"]
    authority_sha256: Digest


class AdapterIdentity(StrictContract):
    adapter_id: Literal["cernora-reference-adapter"]
    adapter_version: Literal["1"]
    authority_sha256: Digest


class ReportComponents(StrictContract):
    task: TaskIdentity
    container: ContainerIdentity
    harness: HarnessIdentity
    runtime: RuntimeIdentity
    model: ModelIdentity
    cernora: CernoraIdentity
    profile: ProfileIdentity
    adapter: AdapterIdentity


class AttemptRecord(StrictContract):
    ordinal: Literal[1, 2]
    attempt_id: Digest
    predecessor_attempt_id: Digest | None
    retry_delay_seconds: Literal[10] | None
    source_trial_id: Identifier
    lifecycle_outcome: TerminalState
    lifecycle_reason_code: ReasonCode
    retry_eligible: StrictBool
    completed_export_sha256: DigestOrMissing
    candidate_tree_sha256: DigestOrMissing
    artifact_manifest_sha256: DigestOrMissing

    @model_validator(mode="after")
    def validate_lifecycle(self) -> AttemptRecord:
        eligible = self.lifecycle_outcome in {
            "infrastructure-start-failure",
            "transient-provider-pre-terminal",
        }
        if self.retry_eligible is not eligible:
            raise ValueError("attempt retry eligibility contradicts lifecycle outcome")
        if self.ordinal == 1 and (
            self.predecessor_attempt_id is not None or self.retry_delay_seconds is not None
        ):
            raise ValueError("first attempt cannot have a retry predecessor or delay")
        if self.ordinal == 2 and (
            self.predecessor_attempt_id is None or self.retry_delay_seconds != 10
        ):
            raise ValueError("second attempt requires its predecessor and the fixed retry delay")
        return self


class TestRunnerEvidence(StrictContract):
    authority_id: Literal["tiny-calculator-test-runner", "tiny-calculator-v2-test-runner"]
    authority_version: Literal["1"]
    authority_sha256: Digest
    test_plan_sha256: DigestOrMissing
    test_results_sha256: DigestOrMissing
    process_receipt_sha256: DigestOrMissing
    resource_receipt_sha256: DigestOrMissing


class StrictReloadVerified(StrictContract):
    status: Literal["verified"]
    result_identity_sha256: Digest
    bundle_id: Identifier
    evidence_bundle_sha256: Digest
    evaluation_input_sha256: Digest
    evaluation_id: Identifier
    evidence_id: Identifier
    score_id: Identifier
    decision_id: Identifier
    gate_decision_sha256: Digest
    evaluation_receipt_sha256: Digest

    @model_validator(mode="after")
    def validate_identity(self) -> StrictReloadVerified:
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"result_identity_sha256"})
        )
        if self.result_identity_sha256 != expected:
            raise ValueError("strict-reload result identity mismatch")
        return self

    @staticmethod
    def compute_identity(payload: Mapping[str, Any]) -> str:
        return canonical_content_id(payload, excluded=frozenset({"result_identity_sha256"}))


class StrictReloadUnavailable(StrictContract):
    status: Literal["failed", "not-performed"]
    reason: MissingReason


StrictReload = Annotated[
    StrictReloadVerified | StrictReloadUnavailable,
    Field(discriminator="status"),
]


class EvaluationOutcome(StrictContract):
    validity: Literal["valid", "invalid", "unavailable"]
    behavioral_decision: Literal["pass", "fail", "inconclusive"]
    gate_decision: Literal["pass", "fail", "inconclusive"]
    strict_reload: StrictReload

    @model_validator(mode="after")
    def keep_conclusions_separate_and_consistent(self) -> EvaluationOutcome:
        if self.validity == "valid":
            if self.behavioral_decision not in {"pass", "fail"}:
                raise ValueError("a valid evaluation requires a behavioral pass or fail")
            if self.gate_decision != self.behavioral_decision:
                raise ValueError("valid behavioral and gate decisions must agree")
        elif self.behavioral_decision != "inconclusive" or self.gate_decision != "inconclusive":
            raise ValueError("invalid or unavailable evaluation cannot claim pass or fail")
        if self.validity == "unavailable" and self.strict_reload.status == "verified":
            raise ValueError("unavailable evaluation cannot claim a verified strict reload")
        return self


class DiagnosticData(StrictContract):
    duration: MetricOrMissing
    peak_memory: MetricOrMissing
    cpu_time: MetricOrMissing
    input_tokens: MetricOrMissing
    output_tokens: MetricOrMissing
    total_tokens: MetricOrMissing
    network_capable_commands: NetworkCommandsOrMissing
    runtime_boundary_observation_sha256: DigestOrMissing

    @model_validator(mode="after")
    def validate_units(self) -> DiagnosticData:
        expected = {
            "duration": "milliseconds",
            "peak_memory": "bytes",
            "cpu_time": "milliseconds",
            "input_tokens": "tokens",
            "output_tokens": "tokens",
            "total_tokens": "tokens",
        }
        for field_name, unit in expected.items():
            value = getattr(self, field_name)
            if isinstance(value, AvailableMetric) and value.unit != unit:
                raise ValueError(f"{field_name} must use {unit}")
        return self


class OfflineInput(StrictContract):
    path: NonEmpty
    sha256: Digest

    @model_validator(mode="after")
    def validate_path(self) -> OfflineInput:
        validate_relative_path(self.path)
        return self


class OfflineCommand(StrictContract):
    purpose: Literal["adapt", "evaluate", "strict-reload"]
    working_directory: Literal["."]
    argv: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]


class OfflineRebuildAvailable(StrictContract):
    status: Literal["available"]
    experiment_spec: OfflineInput
    completed_export: OfflineInput
    adapted_bundle: OfflineInput
    evaluation_output: OfflineInput
    commands: Annotated[tuple[OfflineCommand, ...], Field(min_length=3, max_length=3)]

    @model_validator(mode="after")
    def validate_commands(self) -> OfflineRebuildAvailable:
        if tuple(command.purpose for command in self.commands) != (
            "adapt",
            "evaluate",
            "strict-reload",
        ):
            raise ValueError("offline commands must be ordered adapt, evaluate, strict-reload")
        return self


class OfflineRebuildUnavailable(StrictContract):
    status: Literal["missing"]
    reason: MissingReason


OfflineRebuild = Annotated[
    OfflineRebuildAvailable | OfflineRebuildUnavailable,
    Field(discriminator="status"),
]


def _iter_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            strings.extend(_iter_strings(key))
            strings.extend(_iter_strings(item))
    elif isinstance(value, list | tuple):
        for item in value:
            strings.extend(_iter_strings(item))
    return strings


def _validate_portability(payload: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(payload)
    for kind, pattern in PATTERNS:
        if pattern.search(encoded):
            raise ReportError(f"report contains prohibited secret pattern: {kind}")
    for value in _iter_strings(payload):
        if "`" in value or any(ord(character) < 32 for character in value):
            raise ReportError("report contains unsafe control or Markdown delimiter characters")
        if _HOST_PATH.search(value):
            raise ReportError("report contains an absolute host path")
        if _ENV_ASSIGNMENT.search(value):
            raise ReportError("report contains a raw environment assignment")
        if _ACCOUNT_VALUE.search(value) or _EMAIL.search(value):
            raise ReportError("report contains account-identifying data")


class RunReport(StrictContract):
    schema_version: Literal["cernora.reference.run-report/v1"]
    report_id: Digest
    experiment_id: Digest
    components: ReportComponents
    attempts: Annotated[tuple[AttemptRecord, ...], Field(min_length=1, max_length=2)]
    selected_attempt_id: Digest
    evaluation: EvaluationOutcome
    test_runner: TestRunnerEvidence
    diagnostics: DiagnosticData
    offline_rebuild: OfflineRebuild

    @model_validator(mode="after")
    def validate_identity_attempts_and_portability(self) -> RunReport:
        if tuple(attempt.ordinal for attempt in self.attempts) != tuple(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("attempts must be in complete ordinal order")
        if len({attempt.attempt_id for attempt in self.attempts}) != len(self.attempts):
            raise ValueError("attempt identities must be unique")
        if len(self.attempts) == 2:
            first, second = self.attempts
            if not first.retry_eligible or second.predecessor_attempt_id != first.attempt_id:
                raise ValueError("second attempt is not the authorized retry of the first")
        if self.selected_attempt_id != self.attempts[-1].attempt_id:
            raise ValueError("selected attempt must follow the frozen retry policy")
        selected = self.attempts[-1]
        selected_digests = (
            selected.completed_export_sha256,
            selected.candidate_tree_sha256,
            selected.artifact_manifest_sha256,
        )
        if selected.lifecycle_outcome in {"completed", "behavioral-failure"} and any(
            isinstance(value, MissingData) for value in selected_digests
        ):
            raise ValueError("completed lifecycle must bind all export identities")
        receipt_digests = (
            self.test_runner.test_plan_sha256,
            self.test_runner.test_results_sha256,
            self.test_runner.process_receipt_sha256,
            self.test_runner.resource_receipt_sha256,
        )
        if self.evaluation.validity == "valid":
            if self.evaluation.strict_reload.status != "verified":
                raise ValueError("valid evaluation requires a verified strict reload")
            if any(isinstance(value, MissingData) for value in receipt_digests):
                raise ValueError("valid evaluation must bind every Test Runner receipt")
            expected_lifecycle = (
                "completed"
                if self.evaluation.behavioral_decision == "pass"
                else "behavioral-failure"
            )
            if selected.lifecycle_outcome != expected_lifecycle:
                raise ValueError("behavioral decision contradicts selected lifecycle outcome")
        if self.evaluation.strict_reload.status == "verified" and not isinstance(
            self.offline_rebuild, OfflineRebuildAvailable
        ):
            raise ValueError("verified strict reload requires portable offline rebuild inputs")
        payload = self.model_dump(mode="json")
        _validate_portability(payload)
        expected = self.compute_report_id(payload)
        if self.report_id != expected:
            raise ValueError("report_id does not match canonical report content")
        return self

    @staticmethod
    def compute_report_id(payload: Mapping[str, Any]) -> str:
        return canonical_content_id(payload, excluded=frozenset({"report_id"}))

    @classmethod
    def from_file(cls, path: Path) -> RunReport:
        payload = load_json_file(path)
        if not isinstance(payload, dict):
            raise ReportError("run report must be a JSON object")
        try:
            report = cls.model_validate(payload)
        except ValueError as exc:
            raise ReportError(f"invalid run report: {exc}") from exc
        if path.read_bytes() != report.canonical_bytes():
            raise ReportError("run report is not canonical JSON")
        return report

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def markdown(self) -> str:
        """Render a deterministic view; the validated JSON model remains authoritative."""

        def digest(value: DigestOrMissing) -> str:
            if isinstance(value, AvailableDigest):
                return value.sha256
            return f"missing ({value.reason})"

        def metric(value: MetricOrMissing) -> str:
            if isinstance(value, AvailableMetric):
                return f"{value.value} {value.unit}"
            return f"missing ({value.reason})"

        lines = [
            "# Cernora reference workflow run report",
            "",
            (
                "> This Markdown file is a deterministic rendering. "
                "`run-report.json` is authoritative."
            ),
            "",
            "## Identity",
            "",
            f"- Report: `{self.report_id}`",
            f"- Experiment: `{self.experiment_id}`",
            (
                f"- Task: `{self.components.task.task_id}` "
                f"version `{self.components.task.task_version}`"
            ),
            f"- Image: `{self.components.container.image}`",
            (f"- Harness: `{self.components.harness.name}` `{self.components.harness.version}`"),
            (f"- Runtime: `{self.components.runtime.name}` `{self.components.runtime.version}`"),
            (
                f"- Model: `{self.components.model.name}` "
                f"(reasoning `{self.components.model.reasoning_effort}`)"
            ),
            f"- Cernora wheel: `0.1.2` `{self.components.cernora.wheel_sha256}`",
            (
                f"- Profile: `{self.components.profile.profile_id}` "
                f"version `{self.components.profile.profile_version}`"
            ),
            (
                f"- Adapter: `{self.components.adapter.adapter_id}` "
                f"version `{self.components.adapter.adapter_version}`"
            ),
            "",
            "## Attempts",
            "",
            "| # | Attempt | Predecessor | Lifecycle | Export | Candidate tree | Manifest |",
            "| ---: | --- | --- | --- | --- | --- | --- |",
        ]
        for attempt in self.attempts:
            predecessor = attempt.predecessor_attempt_id or "none"
            lines.append(
                f"| {attempt.ordinal} | `{attempt.attempt_id}` | `{predecessor}` | "
                f"`{attempt.lifecycle_outcome}` | `{digest(attempt.completed_export_sha256)}` | "
                f"`{digest(attempt.candidate_tree_sha256)}` | "
                f"`{digest(attempt.artifact_manifest_sha256)}` |"
            )
        lines.extend(
            [
                "",
                f"Selected attempt: `{self.selected_attempt_id}`.",
                "",
                "## Evaluation",
                "",
                f"- Lifecycle: `{self.attempts[-1].lifecycle_outcome}`",
                f"- Evaluation validity: `{self.evaluation.validity}`",
                f"- Behavioral decision: `{self.evaluation.behavioral_decision}`",
                f"- Gate decision: `{self.evaluation.gate_decision}`",
                f"- Strict reload: `{self.evaluation.strict_reload.status}`",
                "",
                "## Test authority",
                "",
                f"- Authority: `{self.test_runner.authority_id}` version `1`",
                f"- Authority digest: `{self.test_runner.authority_sha256}`",
                f"- Test plan: `{digest(self.test_runner.test_plan_sha256)}`",
                f"- Test results: `{digest(self.test_runner.test_results_sha256)}`",
                f"- Process receipt: `{digest(self.test_runner.process_receipt_sha256)}`",
                f"- Resource receipt: `{digest(self.test_runner.resource_receipt_sha256)}`",
                "",
                "## Diagnostics",
                "",
                f"- Duration: {metric(self.diagnostics.duration)}",
                f"- Peak memory: {metric(self.diagnostics.peak_memory)}",
                f"- CPU time: {metric(self.diagnostics.cpu_time)}",
                f"- Input tokens: {metric(self.diagnostics.input_tokens)}",
                f"- Output tokens: {metric(self.diagnostics.output_tokens)}",
                f"- Total tokens: {metric(self.diagnostics.total_tokens)}",
                (
                    "- Runtime boundary observation: "
                    f"{digest(self.diagnostics.runtime_boundary_observation_sha256)}"
                ),
                (
                    "- Network-capable commands: "
                    + (
                        ", ".join(
                            f"{item.command}={item.occurrences}"
                            for item in self.diagnostics.network_capable_commands.observations
                        )
                        or "none observed"
                        if isinstance(
                            self.diagnostics.network_capable_commands,
                            NetworkCommandsAvailable,
                        )
                        else (f"missing ({self.diagnostics.network_capable_commands.reason})")
                    )
                ),
                "",
                "## Offline rebuild",
                "",
            ]
        )
        if isinstance(self.offline_rebuild, OfflineRebuildUnavailable):
            lines.append(f"Unavailable: `{self.offline_rebuild.reason}`.")
        else:
            for command in self.offline_rebuild.commands:
                rendered = canonical_json_bytes(list(command.argv)).decode("utf-8")
                lines.append(f"- `{command.purpose}`: `{rendered}`")
        lines.append("")
        return "\n".join(lines)


def materialize_run_report(payload_without_id: dict[str, object]) -> RunReport:
    if "report_id" in payload_without_id:
        raise ReportError("materialization input must omit report_id")
    payload = dict(payload_without_id)
    payload["report_id"] = RunReport.compute_report_id(payload)
    try:
        return RunReport.model_validate(payload)
    except ValueError as exc:
        raise ReportError(f"invalid run report: {exc}") from exc


def publish_run_report(report: RunReport, destination: Path) -> None:
    """Publish authoritative JSON and its derived Markdown together without replacement."""

    parent = destination.parent.resolve()
    resolved_destination = parent / destination.name
    if not parent.is_dir():
        raise ReportError("report destination parent must already exist")
    if resolved_destination.exists():
        raise ReportError("report destination must not already exist")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=parent))
    try:
        (staging / "run-report.json").write_bytes(report.canonical_bytes())
        (staging / "run-report.md").write_text(report.markdown(), encoding="utf-8", newline="")
        require_secret_free(staging)
        atomic_publish_directory(staging, resolved_destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
