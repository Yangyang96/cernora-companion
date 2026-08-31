"""Fresh development-only Agent pilot authority for the next Priority 4 study."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from cernora import BootstrapPlan
from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    CanonicalAuthoritySource,
    ControlledExperimentSpecV2,
    Digest,
    Identifier,
    StrictV2Contract,
    materialize_authority_source,
)
from cernora_reference_workflow.controlled_run_plan import ControlledTrialSlotV2
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
)
from cernora_reference_workflow.m4_final_plan import build_controlled_specifications
from cernora_reference_workflow.run_plan import (
    ConnectorIdentity,
    RunExecutionPolicy,
)
from cernora_reference_workflow.study_preparation import ImplementationCandidate

PILOT_CASE_IDS = (
    "p4-dev-json-pointer",
    "p4-dev-midnight-window",
    "p4-dev-semver-precedence",
    "p4-reg-cache-key",
    "p4-reg-nested-delete",
    "p4-reg-vary-header",
)
PILOT_BASELINE_PROMPT_TEXT = (
    "Repair the task from its declared behavior and the available workspace evidence."
)
PILOT_TIMEOUT_SECONDS = 300
PILOT_ATTEMPT_ENVELOPE_SECONDS = 360
PILOT_SETUP_TIMEOUT_SECONDS = 1440
PILOT_MAX_ATTEMPTS = 12
PILOT_MAX_WALL_SECONDS = 7200
PILOT_PREFLIGHT_FREE_BYTES = 15 * 1024**3
PILOT_SAFE_STOP_FREE_BYTES = 8 * 1024**3
_CALIBRATION_TIMEOUT_SECONDS: Literal[10] = 10
_CALIBRATION_RUNNER = """\
import runpy
import sys

def deny_network(event, arguments):
    if event.startswith("socket."):
        raise RuntimeError("network disabled")

sys.addaudithook(deny_network)
verifier, candidate = sys.argv[1:]
sys.argv = [verifier, candidate]
runpy.run_path(verifier, run_name="__main__")
"""

PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonEmpty = Annotated[StrictStr, Field(min_length=1)]


class DevelopmentPilotCalibration(StrictV2Contract):
    """Offline verifier calibration; never an Agent observation."""

    case_id: Identifier
    split: Literal["development", "regression"]
    task_authority_sha256: Digest
    verifier_sha256: Digest
    baseline_sha256: Digest
    solution_sha256: Digest
    baseline_exit_code: PositiveInt
    solution_exit_code: Literal[0]
    timeout_seconds: Literal[10]
    network: Literal["python-audit-hook-deny-socket"]
    source: Literal["verifier-calibration"]
    agent_outcome: Literal["not-observed"]


class DevelopmentPilotCorpus(StrictV2Contract):
    schema_version: Literal["cernora.reference.development-pilot-corpus/v1"]
    corpus_id: Digest
    tasks: Annotated[tuple[ControlledTaskAuthority, ...], Field(min_length=6, max_length=6)]
    calibrations: Annotated[
        tuple[DevelopmentPilotCalibration, ...], Field(min_length=6, max_length=6)
    ]

    @field_validator("tasks", "calibrations", mode="before")
    @classmethod
    def tuple_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_fresh_corpus(self) -> Self:
        task_ids = tuple(item.case.case_id for item in self.tasks)
        calibration_ids = tuple(item.case_id for item in self.calibrations)
        splits = tuple(item.split_id for item in self.tasks)
        if task_ids != PILOT_CASE_IDS or calibration_ids != PILOT_CASE_IDS:
            raise ValueError("development pilot corpus does not equal the fresh six-Case set")
        if splits.count("development") != 3 or splits.count("regression") != 3:
            raise ValueError("development pilot corpus requires exact 3/3 visible splits")
        if any(item.split_id == "held-out" for item in self.tasks):
            raise ValueError("development pilot corpus cannot contain held-out material")
        by_case = {item.case.case_id: item for item in self.tasks}
        for calibration in self.calibrations:
            task = by_case[calibration.case_id]
            if (
                calibration.split != task.split_id
                or calibration.task_authority_sha256 != task.authority_sha256
            ):
                raise ValueError("verifier calibration does not bind its task authority")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"corpus_id"})
        )
        if self.corpus_id != expected:
            raise ValueError("development pilot corpus identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class DevelopmentPilotImage(StrictV2Contract):
    case_id: Identifier
    image: NonEmpty

    @model_validator(mode="after")
    def immutable_case_image(self) -> Self:
        prefix = f"cernora-reference/p4-pilot-{self.case_id}@sha256:"
        digest = self.image.removeprefix(prefix)
        if (
            self.image == digest
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("development pilot image must bind its Case and immutable digest")
        return self


class DevelopmentPilotImageSet(StrictV2Contract):
    schema_version: Literal["cernora.reference.development-pilot-images/v1"]
    image_set_id: Digest
    build_base_image: NonEmpty
    platform: Literal["linux/arm64"]
    images: Annotated[tuple[DevelopmentPilotImage, ...], Field(min_length=6, max_length=6)]

    @field_validator("images", mode="before")
    @classmethod
    def tuple_images(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def canonical_image_set(self) -> Self:
        case_ids = tuple(item.case_id for item in self.images)
        if case_ids != PILOT_CASE_IDS:
            raise ValueError("development pilot images do not equal the fresh Case set")
        marker = "@sha256:"
        if marker not in self.build_base_image:
            raise ValueError("development pilot build base must be immutable")
        digest = self.build_base_image.rsplit(marker, 1)[1]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("development pilot build base digest is invalid")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"image_set_id"})
        )
        if self.image_set_id != expected:
            raise ValueError("development pilot image set identity mismatch")
        return self

    @classmethod
    def from_bytes(cls, data: bytes) -> DevelopmentPilotImageSet:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise ContractError("development pilot image set must be one JSON object")
        value = cls.model_validate(payload)
        if data != value.canonical_bytes():
            raise ContractError("development pilot image set is not canonical JSON")
        return value

    @classmethod
    def from_file(cls, path: Path) -> DevelopmentPilotImageSet:
        return cls.from_bytes(read_regular_file_bytes(path))

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def materialize_development_pilot_image_set(
    *, build_base_image: str, images: Mapping[str, str]
) -> DevelopmentPilotImageSet:
    if set(images) != set(PILOT_CASE_IDS):
        raise ContractError("development pilot image input does not equal the fresh Case set")
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-images/v1",
        "build_base_image": build_base_image,
        "platform": "linux/arm64",
        "images": [{"case_id": case_id, "image": images[case_id]} for case_id in PILOT_CASE_IDS],
    }
    payload["image_set_id"] = canonical_content_id(payload, excluded=frozenset())
    return DevelopmentPilotImageSet.model_validate(payload)


class DevelopmentPilotStopPolicy(StrictV2Contract):
    no_behavioral_failure: Literal["stop-no-candidate"]
    incomplete_or_missing_evidence: Literal["stop-inconclusive"]
    ambiguous_active_attempt: Literal["pause-no-retry"]
    completion: Literal["stop-before-candidate-construction"]


class DevelopmentAgentPilotPlan(StrictV2Contract):
    """Exact authority requested for the bounded development-only Agent pilot."""

    schema_version: Literal[
        "cernora.reference.development-agent-pilot-plan/v1",
        "cernora.reference.development-agent-pilot-plan/v2",
        "cernora.reference.development-agent-pilot-plan/v3",
    ]
    plan_id: Digest
    selected_study_mode: Literal["confirmatory-effect"]
    authority_scope: Literal["development-only-agent-pilot"]
    execution_authorized: Literal[False]
    treatment_axis_if_eligible: Literal["prompt-instruction"]
    corpus: DevelopmentPilotCorpus
    images: DevelopmentPilotImageSet
    implementation_candidates: tuple[ImplementationCandidate, ...] | None = None
    baseline_prompt: CanonicalAuthoritySource
    connector: ConnectorIdentity
    experiment_specs: Annotated[
        tuple[ControlledExperimentSpecV2, ...], Field(min_length=6, max_length=6)
    ]
    repetitions: Literal[1]
    planned_trial_count: Literal[6]
    worst_case_attempt_count: Literal[12]
    attempt_envelope_timeout_seconds: Literal[360] | None = None
    execution: RunExecutionPolicy
    preflight_free_bytes: Literal[16106127360]
    safe_stop_free_bytes: Literal[8589934592]
    external_provider_scope: Literal["openai-codex-authenticated-generation-only"]
    custody_policy: Literal["new-durable-git-ignored-directory"]
    stop_policy: DevelopmentPilotStopPolicy
    prohibited_actions: tuple[
        Literal[
            "held-out-access",
            "smoke-execution",
            "study-start-execution",
            "study-step-execution",
            "54-trial-matrix",
        ],
        ...,
    ]

    @field_validator(
        "experiment_specs", "implementation_candidates", "prohibited_actions", mode="before"
    )
    @classmethod
    def tuple_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_development_authority(self) -> Self:
        tasks = self.corpus.tasks
        case_ids = tuple(item.case.case_id for item in tasks)
        spec_ids = tuple(item.task.task_id for item in self.experiment_specs)
        image_by_case = {item.case_id: item.image for item in self.images.images}
        expected_prohibitions = (
            "held-out-access",
            "smoke-execution",
            "study-start-execution",
            "study-step-execution",
            "54-trial-matrix",
        )
        if self.schema_version == "cernora.reference.development-agent-pilot-plan/v1":
            if self.implementation_candidates is not None:
                raise ValueError("legacy development pilot Plan cannot bind implementations")
        elif self.implementation_candidates is None or tuple(
            item.name for item in self.implementation_candidates
        ) != ("cernora", "cernora-reference-workflow"):
            raise ValueError("development pilot implementation authority is incomplete")
        if self.schema_version == "cernora.reference.development-agent-pilot-plan/v3":
            if self.attempt_envelope_timeout_seconds != PILOT_ATTEMPT_ENVELOPE_SECONDS:
                raise ValueError("development pilot Attempt envelope authority drifted")
        elif self.attempt_envelope_timeout_seconds is not None:
            raise ValueError("legacy development pilot Plan cannot bind an Attempt envelope")
        if spec_ids != case_ids or self.prohibited_actions != expected_prohibitions:
            raise ValueError("development pilot matrix or prohibitions are not exact")
        if self.baseline_prompt.source_id != "p4-confirmatory-baseline-prompt-v1":
            raise ValueError("development pilot baseline prompt authority is not selected")
        for task, spec in zip(tasks, self.experiment_specs, strict=True):
            if (
                spec.configuration_id != "baseline"
                or spec.prompt_source != self.baseline_prompt
                or spec.task.authority_id != task.authority_id
                or spec.task.authority_sha256 != task.authority_sha256
                or spec.container.image != image_by_case[task.case.case_id]
                or spec.container.build_base_image != self.images.build_base_image
                or spec.container.platform != self.images.platform
                or spec.limits.timeout_seconds != PILOT_TIMEOUT_SECONDS
                or spec.limits.agent_setup_timeout_seconds != PILOT_SETUP_TIMEOUT_SECONDS
                or spec.retry.max_retries != 1
            ):
                raise ValueError("development pilot Experiment authority drifted")
        if (
            self.execution.concurrency != 1
            or self.execution.max_attempt_count != PILOT_MAX_ATTEMPTS
            or self.execution.max_total_wall_time_seconds != PILOT_MAX_WALL_SECONDS
            or self.worst_case_attempt_count
            != sum(1 + item.retry.max_retries for item in self.experiment_specs)
        ):
            raise ValueError("development pilot bounds do not equal the exact retry matrix")
        identity = self.model_dump(mode="json")
        if self.implementation_candidates is None:
            identity.pop("implementation_candidates")
        if self.attempt_envelope_timeout_seconds is None:
            identity.pop("attempt_envelope_timeout_seconds")
        expected = canonical_content_id(identity, excluded=frozenset({"plan_id"}))
        if self.plan_id != expected:
            raise ValueError("development Agent pilot Plan identity mismatch")
        return self

    @classmethod
    def from_bytes(cls, data: bytes) -> DevelopmentAgentPilotPlan:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise ContractError("development Agent pilot Plan must be one JSON object")
        value = cls.model_validate(payload)
        if data != value.canonical_bytes():
            raise ContractError("development Agent pilot Plan is not canonical JSON")
        return value

    @classmethod
    def from_file(cls, path: Path) -> DevelopmentAgentPilotPlan:
        return cls.from_bytes(read_regular_file_bytes(path))

    def canonical_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        if self.implementation_candidates is None:
            payload.pop("implementation_candidates")
        if self.attempt_envelope_timeout_seconds is None:
            payload.pop("attempt_envelope_timeout_seconds")
        return canonical_json_bytes(payload)

    def expand_trial_slots(self) -> tuple[ControlledTrialSlotV2, ...]:
        slots: list[ControlledTrialSlotV2] = []
        for index, specification in enumerate(self.experiment_specs, start=1):
            identity = {
                "case_id": specification.task.task_id,
                "configuration_id": "baseline",
                "experiment_id": specification.experiment_id,
                "repetition": 1,
                "run_plan_id": self.plan_id,
            }
            slots.append(
                ControlledTrialSlotV2(
                    schema_version="cernora.reference.controlled-trial-slot/v2",
                    trial_slot_id=canonical_content_id(identity, excluded=frozenset()),
                    run_plan_id=self.plan_id,
                    slot_index=index,
                    case_id=specification.task.task_id,
                    configuration_id="baseline",
                    experiment_id=specification.experiment_id,
                    repetition=1,
                )
            )
        return tuple(slots)


def _calibration_environment() -> dict[str, str]:
    return {
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "TZ": "UTC",
    }


def _calibrate(case_root: Path, task: ControlledTaskAuthority) -> DevelopmentPilotCalibration:
    if task.split_id == "held-out":
        raise ContractError("development corpus calibration cannot consume held-out material")
    split = task.split_id
    files = closed_regular_tree(case_root)
    results: dict[str, subprocess.CompletedProcess[bytes]] = {}
    for name in ("baseline", "solution"):
        try:
            results[name] = subprocess.run(
                (
                    sys.executable,
                    "-I",
                    "-c",
                    _CALIBRATION_RUNNER,
                    str(files["verify.py"]),
                    str(files[f"{name}.py"]),
                ),
                cwd=case_root,
                env=_calibration_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_CALIBRATION_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContractError(
                "development corpus verifier calibration could not complete"
            ) from exc
    if results["baseline"].returncode <= 0 or results["solution"].returncode != 0:
        raise ContractError(
            "development corpus does not have a failing baseline and passing solution"
        )
    return DevelopmentPilotCalibration(
        case_id=task.case.case_id,
        split=split,
        task_authority_sha256=task.authority_sha256,
        verifier_sha256=sha256_bytes(read_regular_file_bytes(files["verify.py"])),
        baseline_sha256=sha256_bytes(read_regular_file_bytes(files["baseline.py"])),
        solution_sha256=sha256_bytes(read_regular_file_bytes(files["solution.py"])),
        baseline_exit_code=results["baseline"].returncode,
        solution_exit_code=0,
        timeout_seconds=_CALIBRATION_TIMEOUT_SECONDS,
        network="python-audit-hook-deny-socket",
        source="verifier-calibration",
        agent_outcome="not-observed",
    )


def load_development_pilot_corpus(root: Path) -> DevelopmentPilotCorpus:
    """Strictly load and offline-calibrate the fresh development/regression corpus."""

    if not root.is_dir() or root.is_symlink():
        raise ContractError("development pilot corpus root must be one real directory")
    root = root.resolve(strict=True)
    roots = tuple(sorted(root.iterdir()))
    if len(roots) != 6 or any(not path.is_dir() or path.is_symlink() for path in roots):
        raise ContractError("development pilot corpus must be a closed six-directory tree")
    tasks = tuple(
        sorted((load_visible_task(path) for path in roots), key=lambda item: item.case.case_id)
    )
    by_id = {task.case.case_id: task for task in tasks}
    if (
        tuple(by_id) != PILOT_CASE_IDS
        or sum(item.split_id == "development" for item in tasks) != 3
        or sum(item.split_id == "regression" for item in tasks) != 3
        or any(item.split_id == "held-out" for item in tasks)
    ):
        raise ContractError("development pilot corpus Case identities are not fresh and exact")
    root_by_id: dict[str, Path] = {}
    for path in roots:
        task = load_visible_task(path)
        root_by_id[task.case.case_id] = path
    calibrations = tuple(
        _calibrate(root_by_id[case_id], by_id[case_id]) for case_id in PILOT_CASE_IDS
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-corpus/v1",
        "tasks": [item.model_dump(mode="json") for item in tasks],
        "calibrations": [item.model_dump(mode="json") for item in calibrations],
    }
    payload["corpus_id"] = canonical_content_id(payload, excluded=frozenset())
    return DevelopmentPilotCorpus.model_validate(payload)


def build_development_agent_pilot_plan(
    *,
    corpus: DevelopmentPilotCorpus,
    images: DevelopmentPilotImageSet,
    implementation_candidates: tuple[ImplementationCandidate, ...],
) -> DevelopmentAgentPilotPlan:
    """Freeze the exact baseline-only pilot without granting execution authority."""

    baseline = materialize_authority_source(
        "p4-confirmatory-baseline-prompt-v1", {"text": PILOT_BASELINE_PROMPT_TEXT}
    )
    image_by_case = {item.case_id: item.image for item in images.images}
    specs = build_controlled_specifications(
        tasks=corpus.tasks,
        images=image_by_case,
        build_base_image=images.build_base_image,
        configurations=(("baseline", baseline),),
        bootstrap=BootstrapPlan(
            method="case-clustered-paired-bootstrap/v1",
            confidence_basis_points=9500,
            resamples=10000,
            percentile="nearest_rank_closed",
            seed_source="comparison_input_sha256",
        ),
        pass_k=None,
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-agent-pilot-plan/v3",
        "selected_study_mode": "confirmatory-effect",
        "authority_scope": "development-only-agent-pilot",
        "execution_authorized": False,
        "treatment_axis_if_eligible": "prompt-instruction",
        "corpus": corpus.model_dump(mode="json"),
        "images": images.model_dump(mode="json"),
        "implementation_candidates": [
            item.model_dump(mode="json") for item in implementation_candidates
        ],
        "baseline_prompt": baseline.model_dump(mode="json"),
        "connector": {
            "connector_id": "cernora-reference-harbor-codex",
            "connector_version": "1",
            "platform_qualification": "macos-arm64",
        },
        "experiment_specs": [item.model_dump(mode="json") for item in specs],
        "repetitions": 1,
        "planned_trial_count": 6,
        "worst_case_attempt_count": 12,
        "attempt_envelope_timeout_seconds": PILOT_ATTEMPT_ENVELOPE_SECONDS,
        "execution": {
            "concurrency": 1,
            "max_attempt_count": PILOT_MAX_ATTEMPTS,
            "max_total_wall_time_seconds": PILOT_MAX_WALL_SECONDS,
            "token_budget": {
                "status": "unavailable",
                "reason": "no-structured-authoritative-source",
            },
            "monetary_budget": {
                "status": "unavailable",
                "reason": "no-structured-authoritative-source",
            },
        },
        "preflight_free_bytes": PILOT_PREFLIGHT_FREE_BYTES,
        "safe_stop_free_bytes": PILOT_SAFE_STOP_FREE_BYTES,
        "external_provider_scope": "openai-codex-authenticated-generation-only",
        "custody_policy": "new-durable-git-ignored-directory",
        "stop_policy": {
            "no_behavioral_failure": "stop-no-candidate",
            "incomplete_or_missing_evidence": "stop-inconclusive",
            "ambiguous_active_attempt": "pause-no-retry",
            "completion": "stop-before-candidate-construction",
        },
        "prohibited_actions": [
            "held-out-access",
            "smoke-execution",
            "study-start-execution",
            "study-step-execution",
            "54-trial-matrix",
        ],
    }
    payload["plan_id"] = canonical_content_id(payload, excluded=frozenset())
    return DevelopmentAgentPilotPlan.model_validate(payload)


__all__ = [
    "PILOT_ATTEMPT_ENVELOPE_SECONDS",
    "PILOT_BASELINE_PROMPT_TEXT",
    "PILOT_CASE_IDS",
    "PILOT_MAX_ATTEMPTS",
    "PILOT_MAX_WALL_SECONDS",
    "DevelopmentAgentPilotPlan",
    "DevelopmentPilotCalibration",
    "DevelopmentPilotCorpus",
    "DevelopmentPilotImage",
    "DevelopmentPilotImageSet",
    "build_development_agent_pilot_plan",
    "load_development_pilot_corpus",
    "materialize_development_pilot_image_set",
]
