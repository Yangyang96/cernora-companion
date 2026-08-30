"""Strict offline worksheet for a not-yet-authoritative Controlled Study."""

from __future__ import annotations

import os
import secrets
import tempfile
import zipfile
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import Field, StrictInt, StrictStr, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_study import materialize_study_analysis_policy
from cernora_reference_workflow.experiment_spec import Digest, StrictContract

DecisionId = Literal[
    "fresh-candidate",
    "fresh-heldout-commitment",
    "fresh-heldout-custodian",
    "independent-reviewer",
    "implementation-lock",
    "scientific-question",
    "study-administration",
    "study-design",
]
NonEmpty = Annotated[StrictStr, Field(min_length=1)]
ImplementationName = Literal["cernora", "cernora-reference-workflow"]
ImplementationVersion = Annotated[
    StrictStr, Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9.!+_-]*$")
]
BundlePath = Literal["analysis-policy-proposal.json", "decisions.json", "dossier.md"]

_EXPECTED_DECISIONS: tuple[DecisionId, ...] = (
    "fresh-candidate",
    "fresh-heldout-commitment",
    "fresh-heldout-custodian",
    "independent-reviewer",
    "implementation-lock",
    "scientific-question",
    "study-administration",
    "study-design",
)
_EXPECTED_FILES: tuple[BundlePath, ...] = (
    "analysis-policy-proposal.json",
    "decisions.json",
    "dossier.md",
)


class PendingDecision(StrictContract):
    decision_id: DecisionId
    status: Literal["pending"]
    required_evidence: NonEmpty


class StudyPreparationBoundsProposal(StrictContract):
    case_count: Literal[9]
    configuration_count: Literal[2]
    repetitions: Literal[3]
    planned_trial_count: Literal[54]
    maximum_attempt_count: Literal[108]
    maximum_wall_seconds: Literal[43200]

    @model_validator(mode="after")
    def derived_counts(self) -> Self:
        if self.planned_trial_count != (
            self.case_count * self.configuration_count * self.repetitions
        ):
            raise ValueError("proposed Trial count is not the recommended M4 matrix")
        if self.maximum_attempt_count != self.planned_trial_count * 2:
            raise ValueError("proposed Attempt count is not the recommended retry bound")
        return self


class StudyDesignProposal(StrictContract):
    """Non-binding recommendation that cannot substitute for caller-owned Study choices."""

    status: Literal["non-binding-proposal"]
    recommended_mode: Literal["confirmatory-effect"]
    primary_scope: Literal["held-out"]
    bounds: StudyPreparationBoundsProposal


class ImplementationCandidate(StrictContract):
    name: ImplementationName
    version: ImplementationVersion
    kind: Literal["wheel"]
    size: Annotated[StrictInt, Field(gt=0)]
    sha256: Digest


class PreparationBundleFile(StrictContract):
    path: BundlePath
    size: Annotated[StrictInt, Field(gt=0)]
    sha256: Digest


class StudyPreparationManifest(StrictContract):
    """Closed review bundle that deliberately carries no Study authority."""

    schema_version: Literal["cernora.reference.study-preparation/v1"]
    preparation_id: Digest
    preparation_nonce: Digest
    status: Literal["awaiting-user-decisions"]
    dossier_status: Literal["draft"]
    design_proposal: StudyDesignProposal
    pending_decisions: tuple[PendingDecision, ...]
    implementation_candidates: tuple[ImplementationCandidate, ...]
    files: tuple[PreparationBundleFile, ...]

    @model_validator(mode="after")
    def closed_preparation(self) -> Self:
        if tuple(item.decision_id for item in self.pending_decisions) != _EXPECTED_DECISIONS:
            raise ValueError("preparation decisions are not complete and canonically ordered")
        if tuple(item.name for item in self.implementation_candidates) != (
            "cernora",
            "cernora-reference-workflow",
        ):
            raise ValueError("implementation candidates are not complete and ordered")
        if tuple(item.path for item in self.files) != _EXPECTED_FILES:
            raise ValueError("preparation bundle file index is not closed and ordered")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"preparation_id"})
        )
        if self.preparation_id != expected:
            raise ValueError("Study preparation identity does not match canonical content")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def _wheel_identity(
    path: Path,
    *,
    expected_name: ImplementationName,
    expected_version: str,
) -> tuple[ImplementationName, ImplementationVersion, bytes]:
    data = read_regular_file_bytes(path, maximum=None)
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            metadata_names = sorted(
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            )
            if len(metadata_names) != 1:
                raise ContractError("implementation candidate wheel identity is ambiguous")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ContractError("implementation candidate wheel identity is invalid") from exc
    name = metadata.get("Name", "").lower().replace("_", "-")
    version = metadata.get("Version", "")
    if name != expected_name or version != expected_version:
        raise ContractError("implementation candidate wheel identity does not match preparation")
    return cast(ImplementationName, name), version, data


def _candidate(
    path: Path,
    *,
    expected_name: ImplementationName,
    expected_version: str,
) -> ImplementationCandidate:
    name, version, data = _wheel_identity(
        path,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    return ImplementationCandidate(
        name=name,
        version=version,
        kind="wheel",
        size=len(data),
        sha256=sha256_bytes(data),
    )


def _analysis_policy_proposal_bytes() -> bytes:
    policy = materialize_study_analysis_policy(
        {
            "schema_version": "cernora.reference.study-analysis-policy/v1",
            "primary_outcome": "paired-reliable-success-rate-delta",
            "bootstrap_resamples": 10000,
            "confidence_level": "0.95",
            "guardrail_rule": "no-protected-regression",
            "missing_evidence": "inconclusive",
            "claim_source": "held-out-only",
        }
    )
    return canonical_json_bytes(policy.model_dump(mode="json"))


def _pending_decisions() -> tuple[PendingDecision, ...]:
    evidence = {
        "fresh-candidate": (
            "A new Candidate Development record based only on development and regression evidence."
        ),
        "fresh-heldout-commitment": (
            "A new opaque commitment created independently for this preparation."
        ),
        "fresh-heldout-custodian": (
            "A named custodian and custody/audit procedure selected by the user."
        ),
        "independent-reviewer": "A reviewer or review task independent of Candidate development.",
        "implementation-lock": (
            "Exact Runtime adapter, Harness, Runtime/task image, platform, and configuration bytes."
        ),
        "scientific-question": (
            "A falsifiable purpose, one Treatment axis, Primary threshold, and Guardrails."
        ),
        "study-administration": (
            "Named owner, planned live window, authorization scope, and durable custody location."
        ),
        "study-design": (
            "A user-selected study mode, claim boundary, accepted bounds, and analysis policy."
        ),
    }
    return tuple(
        PendingDecision(
            decision_id=decision_id,
            status="pending",
            required_evidence=evidence[decision_id],
        )
        for decision_id in _EXPECTED_DECISIONS
    )


def _dossier_bytes(nonce: str) -> bytes:
    return (
        "# Next Priority 4 Controlled Study — offline preparation\n\n"
        "Status: **draft — awaiting user decisions; no live study is authorized**\n\n"
        f"Preparation nonce: `{nonce}`\n\n"
        "This is a review worksheet, not a `StudyIntent` or authorization. The machine-readable "
        "manifest binds the exact local wheel candidates. Its design and analysis values are "
        "explicitly non-binding recommendations until the user chooses the study mode and "
        "scientific question.\n\n"
        "## Pending decisions\n\n"
        "- Select the study mode, claim boundary, accepted bounds, and analysis policy.\n"
        "- Freeze a falsifiable scientific question, Primary threshold, and Guardrails.\n"
        "- Develop and independently review a fresh Candidate without held-out access.\n"
        "- Name an independent custodian and obtain a fresh opaque held-out commitment.\n"
        "- Name an independent reviewer.\n\n"
        "- Lock the remaining Runtime, Harness, image, platform, and configuration bytes.\n"
        "- Name the owner and freeze the live window, custody, and authorization scope.\n\n"
        "## Non-binding recommendation\n\n"
        "If the user chooses `confirmatory-effect`, start from a held-out-primary 9 x 2 x 3 "
        "design, at most 108 Attempts, a 43,200-second wall bound, and the enclosed analysis "
        "policy proposal. Choosing `contract-proof` or different justified bounds requires a "
        "replacement preparation. These values are not Study authority.\n\n"
        "## Exact stop point\n\n"
        "No Study authority has been materialized. No held-out material has been requested or "
        "revealed, no execution authority exists, and no Runtime or provider work is permitted.\n"
    ).encode()


def _file_record(path: BundlePath, data: bytes) -> PreparationBundleFile:
    return PreparationBundleFile(path=path, size=len(data), sha256=sha256_bytes(data))


def create_study_preparation_bundle(
    destination: Path,
    *,
    companion_wheel: Path,
    cernora_wheel: Path,
    companion_version: str,
    cernora_version: str,
    preparation_nonce: str | None = None,
) -> StudyPreparationManifest:
    """Create a new closed offline worksheet without materializing Study authority."""

    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ContractError("Study preparation destination must be new with an existing parent")
    nonce = preparation_nonce or secrets.token_hex(32)
    if len(nonce) != 64 or any(character not in "0123456789abcdef" for character in nonce):
        raise ContractError("Study preparation nonce must be fresh 32-byte lowercase hex")
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
    analysis = _analysis_policy_proposal_bytes()
    decisions = _pending_decisions()
    decisions_bytes = canonical_json_bytes([item.model_dump(mode="json") for item in decisions])
    dossier = _dossier_bytes(nonce)
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.study-preparation/v1",
        "preparation_nonce": nonce,
        "status": "awaiting-user-decisions",
        "dossier_status": "draft",
        "design_proposal": {
            "status": "non-binding-proposal",
            "recommended_mode": "confirmatory-effect",
            "primary_scope": "held-out",
            "bounds": {
                "case_count": 9,
                "configuration_count": 2,
                "repetitions": 3,
                "planned_trial_count": 54,
                "maximum_attempt_count": 108,
                "maximum_wall_seconds": 43200,
            },
        },
        "pending_decisions": [item.model_dump(mode="json") for item in decisions],
        "implementation_candidates": [item.model_dump(mode="json") for item in candidates],
        "files": [
            _file_record("analysis-policy-proposal.json", analysis).model_dump(mode="json"),
            _file_record("decisions.json", decisions_bytes).model_dump(mode="json"),
            _file_record("dossier.md", dossier).model_dump(mode="json"),
        ],
    }
    payload["preparation_id"] = canonical_content_id(payload, excluded=frozenset())
    manifest = StudyPreparationManifest.model_validate(payload)
    with tempfile.TemporaryDirectory(
        prefix="cernora-study-preparation-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        (staging / "analysis-policy-proposal.json").write_bytes(analysis)
        (staging / "decisions.json").write_bytes(decisions_bytes)
        (staging / "dossier.md").write_bytes(dossier)
        (staging / "manifest.json").write_bytes(manifest.canonical_bytes())
        os.replace(staging, destination)
    return verify_study_preparation_bundle(
        destination,
        companion_wheel=companion_wheel,
        cernora_wheel=cernora_wheel,
    )


def inspect_study_preparation_bundle(root: Path) -> StudyPreparationManifest:
    """Structurally inspect a closed preparation without claiming artifact verification."""

    files = closed_regular_tree(root)
    expected_entries = (*_EXPECTED_FILES, "manifest.json")
    if (
        tuple(files) != expected_entries
        or tuple(sorted(child.name for child in root.iterdir())) != expected_entries
    ):
        raise ContractError("Study preparation bundle contains unknown or missing files")
    manifest_bytes = read_regular_file_bytes(files["manifest.json"])
    payload = load_json_bytes(manifest_bytes)
    if not isinstance(payload, dict):
        raise ContractError("Study preparation manifest must be a JSON object")
    manifest = StudyPreparationManifest.model_validate(payload)
    if manifest_bytes != manifest.canonical_bytes():
        raise ContractError("Study preparation manifest is not canonical JSON")
    indexed: dict[BundlePath, PreparationBundleFile] = {item.path: item for item in manifest.files}
    for relative_path in _EXPECTED_FILES:
        data = read_regular_file_bytes(files[relative_path], maximum=None)
        record = indexed[relative_path]
        if len(data) != record.size or sha256_bytes(data) != record.sha256:
            raise ContractError(
                f"Study preparation bundle file does not match manifest: {relative_path}"
            )
    decisions_payload = load_json_bytes(read_regular_file_bytes(files["decisions.json"]))
    if decisions_payload != [item.model_dump(mode="json") for item in manifest.pending_decisions]:
        raise ContractError("Study preparation decisions do not match manifest")
    analysis = read_regular_file_bytes(files["analysis-policy-proposal.json"])
    if analysis != _analysis_policy_proposal_bytes():
        raise ContractError("Study preparation analysis policy proposal is not canonical")
    return manifest


def verify_study_preparation_bundle(
    root: Path,
    *,
    companion_wheel: Path,
    cernora_wheel: Path,
) -> StudyPreparationManifest:
    """Strictly verify the closed bundle and both exact implementation artifacts."""

    manifest = inspect_study_preparation_bundle(root)
    for wheel_path, expected_name in (
        (cernora_wheel, "cernora"),
        (companion_wheel, "cernora-reference-workflow"),
    ):
        expected = next(
            item for item in manifest.implementation_candidates if item.name == expected_name
        )
        actual = _candidate(
            wheel_path,
            expected_name=expected.name,
            expected_version=expected.version,
        )
        if actual != expected:
            raise ContractError("Study preparation implementation candidate bytes changed")
    return manifest


__all__ = [
    "StudyPreparationManifest",
    "create_study_preparation_bundle",
    "inspect_study_preparation_bundle",
    "verify_study_preparation_bundle",
]
