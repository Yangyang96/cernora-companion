"""Closed offline bundle for requesting one development-only Agent pilot."""

from __future__ import annotations

import importlib.metadata
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated, Literal, Self

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
from cernora_reference_workflow.development_agent_pilot import (
    DevelopmentAgentPilotPlan,
    DevelopmentPilotCorpus,
    DevelopmentPilotImageSet,
    build_development_agent_pilot_plan,
    load_development_pilot_corpus,
)
from cernora_reference_workflow.experiment_spec import Digest, StrictContract
from cernora_reference_workflow.study_preparation import (
    ImplementationCandidate,
    ImplementationName,
    _candidate,
)

BundlePath = Literal[
    "authorization-request.json",
    "corpus.json",
    "images.json",
    "plan.json",
    "review.md",
]
NonEmpty = Annotated[StrictStr, Field(min_length=1)]
_EXPECTED_FILES: tuple[BundlePath, ...] = (
    "authorization-request.json",
    "corpus.json",
    "images.json",
    "plan.json",
    "review.md",
)


class DevelopmentPilotAuthorizationRequest(StrictContract):
    """Exact requested authority; this record is not approval or an acceptance token."""

    schema_version: Literal["cernora.reference.development-pilot-authorization-request/v1"]
    request_id: Digest
    status: Literal["awaiting-user-authorization"]
    plan_id: Digest
    selected_study_mode: Literal["confirmatory-effect"]
    authority_scope: Literal["development-only-agent-pilot"]
    case_authority_sha256: tuple[Digest, ...]
    planned_trial_count: Literal[6]
    maximum_attempt_count: Literal[12]
    per_attempt_timeout_seconds: Literal[300]
    maximum_wall_seconds: Literal[7200]
    concurrency: Literal[1]
    external_provider_scope: Literal["openai-codex-authenticated-generation-only"]
    credential_source: Literal["CODEX_AUTH_JSON_PATH"]
    proxy_sources: tuple[
        Literal["CERNORA_HTTP_PROXY", "CERNORA_HTTPS_PROXY", "CERNORA_ALL_PROXY"], ...
    ]
    custody_subdirectory: NonEmpty
    completion_stop: Literal["before-candidate-construction"]
    no_failure_stop: Literal["no-candidate"]
    missing_evidence_stop: Literal["inconclusive"]
    explicitly_not_authorized: tuple[
        Literal[
            "held-out-access",
            "held-out-reveal",
            "smoke-execution",
            "study-start-execution",
            "study-step-execution",
            "54-trial-matrix",
        ],
        ...,
    ]

    @field_validator(
        "case_authority_sha256", "proxy_sources", "explicitly_not_authorized", mode="before"
    )
    @classmethod
    def tuple_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_request(self) -> Self:
        if (
            len(self.case_authority_sha256) != 6
            or len(set(self.case_authority_sha256)) != 6
            or self.proxy_sources
            != ("CERNORA_HTTP_PROXY", "CERNORA_HTTPS_PROXY", "CERNORA_ALL_PROXY")
            or self.explicitly_not_authorized
            != (
                "held-out-access",
                "held-out-reveal",
                "smoke-execution",
                "study-start-execution",
                "study-step-execution",
                "54-trial-matrix",
            )
            or self.custody_subdirectory != f".agent/custody/development-pilot-{self.plan_id}"
        ):
            raise ValueError("development pilot authorization request is not exact")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"request_id"})
        )
        if self.request_id != expected:
            raise ValueError("development pilot authorization request identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class DevelopmentPilotBundleFile(StrictContract):
    path: BundlePath
    size: Annotated[StrictInt, Field(gt=0)]
    sha256: Digest


class DevelopmentPilotBundleManifest(StrictContract):
    schema_version: Literal["cernora.reference.development-pilot-bundle/v1"]
    bundle_id: Digest
    status: Literal["awaiting-development-pilot-authorization"]
    selected_study_mode: Literal["confirmatory-effect"]
    execution_authorized: Literal[False]
    plan_id: Digest
    corpus_id: Digest
    image_set_id: Digest
    authorization_request_id: Digest
    implementation_candidates: tuple[ImplementationCandidate, ...]
    files: tuple[DevelopmentPilotBundleFile, ...]

    @field_validator("implementation_candidates", "files", mode="before")
    @classmethod
    def tuple_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def closed_bundle(self) -> Self:
        if (
            tuple(item.name for item in self.implementation_candidates)
            != (
                "cernora",
                "cernora-reference-workflow",
            )
            or tuple(item.path for item in self.files) != _EXPECTED_FILES
        ):
            raise ValueError("development pilot bundle index is not closed and ordered")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"bundle_id"})
        )
        if self.bundle_id != expected:
            raise ValueError("development pilot bundle identity mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def _authorization_request(plan: DevelopmentAgentPilotPlan) -> DevelopmentPilotAuthorizationRequest:
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-authorization-request/v1",
        "status": "awaiting-user-authorization",
        "plan_id": plan.plan_id,
        "selected_study_mode": plan.selected_study_mode,
        "authority_scope": plan.authority_scope,
        "case_authority_sha256": [item.authority_sha256 for item in plan.corpus.tasks],
        "planned_trial_count": plan.planned_trial_count,
        "maximum_attempt_count": plan.execution.max_attempt_count,
        "per_attempt_timeout_seconds": plan.experiment_specs[0].limits.timeout_seconds,
        "maximum_wall_seconds": plan.execution.max_total_wall_time_seconds,
        "concurrency": plan.execution.concurrency,
        "external_provider_scope": plan.external_provider_scope,
        "credential_source": "CODEX_AUTH_JSON_PATH",
        "proxy_sources": [
            "CERNORA_HTTP_PROXY",
            "CERNORA_HTTPS_PROXY",
            "CERNORA_ALL_PROXY",
        ],
        "custody_subdirectory": f".agent/custody/development-pilot-{plan.plan_id}",
        "completion_stop": "before-candidate-construction",
        "no_failure_stop": "no-candidate",
        "missing_evidence_stop": "inconclusive",
        "explicitly_not_authorized": [
            "held-out-access",
            "held-out-reveal",
            "smoke-execution",
            "study-start-execution",
            "study-step-execution",
            "54-trial-matrix",
        ],
    }
    payload["request_id"] = canonical_content_id(payload, excluded=frozenset())
    return DevelopmentPilotAuthorizationRequest.model_validate(payload)


def _review_bytes(
    plan: DevelopmentAgentPilotPlan, request: DevelopmentPilotAuthorizationRequest
) -> bytes:
    return (
        "# Priority 4 development-only Agent pilot\n\n"
        "Status: **offline prepared; awaiting explicit development-pilot authorization**\n\n"
        f"Plan: `{plan.plan_id}`\n\n"
        f"Authorization request: `{request.request_id}`\n\n"
        "The selected future study mode is `confirmatory-effect`, with `prompt-instruction` as "
        "the only eligible Candidate Treatment axis. This bundle contains six fresh visible "
        "Cases (three development and three regression), offline verifier calibrations, exact "
        "baseline-only Experiment authorities, task image identities, and bounded execution "
        "controls. Calibration records are not Agent observations.\n\n"
        "Authorization, if granted, covers only six baseline development Trials, at most twelve "
        "Attempts, one at a time, with a 300-second Attempt timeout and a 7,200-second total wall "
        "bound. It does not cover smoke work, held-out access or reveal, Controlled Study "
        "execution, or the 54-Trial matrix.\n\n"
        "After the pilot, missing evidence is inconclusive. If no authoritative behavioral "
        "failure exists, stop with `no-candidate`. Otherwise stop before Candidate construction "
        "so the failure mechanism and one prompt patch can be reviewed separately.\n"
    ).encode()


def _file(path: BundlePath, data: bytes) -> DevelopmentPilotBundleFile:
    return DevelopmentPilotBundleFile(path=path, size=len(data), sha256=sha256_bytes(data))


def create_development_pilot_bundle(
    destination: Path,
    *,
    corpus_root: Path,
    image_authorities: Path,
    companion_wheel: Path,
    cernora_wheel: Path,
    companion_version: str,
    cernora_version: str,
) -> DevelopmentPilotBundleManifest:
    """Create a closed offline request bundle without executing an Agent Attempt."""

    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ContractError("development pilot bundle destination must be new")
    corpus = load_development_pilot_corpus(corpus_root)
    images = DevelopmentPilotImageSet.from_file(image_authorities)
    candidates = tuple(
        sorted(
            (
                _candidate(
                    cernora_wheel,
                    expected_name="cernora",
                    expected_version=cernora_version,
                ),
                _candidate(
                    companion_wheel,
                    expected_name="cernora-reference-workflow",
                    expected_version=companion_version,
                ),
            ),
            key=lambda item: item.name,
        )
    )
    plan = build_development_agent_pilot_plan(
        corpus=corpus,
        images=images,
        implementation_candidates=candidates,
    )
    request = _authorization_request(plan)
    contents: dict[BundlePath, bytes] = {
        "authorization-request.json": request.canonical_bytes(),
        "corpus.json": corpus.canonical_bytes(),
        "images.json": images.canonical_bytes(),
        "plan.json": plan.canonical_bytes(),
        "review.md": _review_bytes(plan, request),
    }
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.development-pilot-bundle/v1",
        "status": "awaiting-development-pilot-authorization",
        "selected_study_mode": "confirmatory-effect",
        "execution_authorized": False,
        "plan_id": plan.plan_id,
        "corpus_id": corpus.corpus_id,
        "image_set_id": images.image_set_id,
        "authorization_request_id": request.request_id,
        "implementation_candidates": [item.model_dump(mode="json") for item in candidates],
        "files": [_file(path, contents[path]).model_dump(mode="json") for path in _EXPECTED_FILES],
    }
    payload["bundle_id"] = canonical_content_id(payload, excluded=frozenset())
    manifest = DevelopmentPilotBundleManifest.model_validate(payload)
    with tempfile.TemporaryDirectory(
        prefix="cernora-development-pilot-", dir=destination.parent
    ) as tmp:
        staging = Path(tmp) / "bundle"
        staging.mkdir()
        for path in _EXPECTED_FILES:
            (staging / path).write_bytes(contents[path])
        (staging / "manifest.json").write_bytes(manifest.canonical_bytes())
        os.replace(staging, destination)
    return verify_development_pilot_bundle(
        destination,
        companion_wheel=companion_wheel,
        cernora_wheel=cernora_wheel,
    )


def inspect_development_pilot_bundle(root: Path) -> DevelopmentPilotBundleManifest:
    files = closed_regular_tree(root)
    expected = tuple(sorted((*_EXPECTED_FILES, "manifest.json")))
    if tuple(files) != expected or tuple(sorted(item.name for item in root.iterdir())) != expected:
        raise ContractError("development pilot bundle contains unknown or missing files")
    manifest_raw = read_regular_file_bytes(files["manifest.json"])
    manifest_payload = load_json_bytes(manifest_raw)
    if not isinstance(manifest_payload, dict):
        raise ContractError("development pilot manifest must be one JSON object")
    manifest = DevelopmentPilotBundleManifest.model_validate(manifest_payload)
    if manifest_raw != manifest.canonical_bytes():
        raise ContractError("development pilot manifest is not canonical JSON")
    indexed = {item.path: item for item in manifest.files}
    for path in _EXPECTED_FILES:
        data = read_regular_file_bytes(files[path], maximum=None)
        if len(data) != indexed[path].size or sha256_bytes(data) != indexed[path].sha256:
            raise ContractError("development pilot bundle file does not match manifest")
    corpus = DevelopmentPilotCorpus.model_validate_json(
        read_regular_file_bytes(files["corpus.json"])
    )
    images = DevelopmentPilotImageSet.from_bytes(read_regular_file_bytes(files["images.json"]))
    plan = DevelopmentAgentPilotPlan.from_bytes(read_regular_file_bytes(files["plan.json"]))
    request = DevelopmentPilotAuthorizationRequest.model_validate_json(
        read_regular_file_bytes(files["authorization-request.json"])
    )
    if (
        corpus.corpus_id != manifest.corpus_id
        or images.image_set_id != manifest.image_set_id
        or plan.plan_id != manifest.plan_id
        or plan.corpus != corpus
        or plan.images != images
        or (
            plan.implementation_candidates is not None
            and plan.implementation_candidates != manifest.implementation_candidates
        )
        or request.plan_id != plan.plan_id
        or request.request_id != manifest.authorization_request_id
        or request.canonical_bytes() != read_regular_file_bytes(files["authorization-request.json"])
    ):
        raise ContractError("development pilot bundle authorities do not close")
    return manifest


def verify_development_pilot_bundle(
    root: Path,
    *,
    companion_wheel: Path,
    cernora_wheel: Path,
) -> DevelopmentPilotBundleManifest:
    manifest = inspect_development_pilot_bundle(root)
    wheel_authorities: tuple[tuple[Path, ImplementationName], ...] = (
        (cernora_wheel, "cernora"),
        (companion_wheel, "cernora-reference-workflow"),
    )
    for wheel, name in wheel_authorities:
        expected = next(item for item in manifest.implementation_candidates if item.name == name)
        actual = _candidate(wheel, expected_name=expected.name, expected_version=expected.version)
        if actual != expected:
            raise ContractError("development pilot implementation candidate bytes changed")
    return manifest


def verify_development_pilot_runtime(
    plan: DevelopmentAgentPilotPlan,
    *,
    repository_root: Path,
    companion_wheel: Path,
    cernora_wheel: Path,
) -> None:
    """Bind the active pilot interpreter to both exact Plan v2 wheel candidates."""

    candidates = plan.implementation_candidates
    if candidates is None:
        raise ContractError("development pilot Runtime requires implementation-bound Plan v2")
    expected_prefix = repository_root.resolve(strict=True) / ".venv"
    if Path(sys.prefix).resolve(strict=True) != expected_prefix.resolve(strict=True):
        raise ContractError("development pilot Runtime interpreter is outside repository .venv")
    expected_by_name = {item.name: item for item in candidates}
    runtime_wheels: tuple[tuple[Path, ImplementationName], ...] = (
        (cernora_wheel, "cernora"),
        (companion_wheel, "cernora-reference-workflow"),
    )
    for wheel, name in runtime_wheels:
        expected = expected_by_name.get(name)
        if expected is None:
            raise ContractError("development pilot Runtime implementation authority is incomplete")
        actual = _candidate(wheel, expected_name=name, expected_version=expected.version)
        if actual != expected:
            raise ContractError("development pilot Runtime wheel bytes changed")
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ContractError("development pilot Runtime distribution is unavailable") from exc
        if distribution.version != expected.version:
            raise ContractError("development pilot Runtime distribution version changed")
        with zipfile.ZipFile(wheel) as archive:
            members = tuple(
                item
                for item in archive.infolist()
                if not item.is_dir() and not item.filename.endswith(".dist-info/RECORD")
            )
            if not members:
                raise ContractError("development pilot Runtime wheel is empty")
            for member in members:
                installed = Path(str(distribution.locate_file(member.filename)))
                if read_regular_file_bytes(installed, maximum=None) != archive.read(member):
                    raise ContractError("development pilot Runtime distribution bytes changed")


__all__ = [
    "DevelopmentPilotAuthorizationRequest",
    "DevelopmentPilotBundleManifest",
    "create_development_pilot_bundle",
    "inspect_development_pilot_bundle",
    "verify_development_pilot_bundle",
    "verify_development_pilot_runtime",
]
