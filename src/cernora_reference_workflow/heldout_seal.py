"""Authenticated, content-identified custody for sealed held-out repair Cases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal, Self

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import Field, JsonValue, StrictInt, StrictStr, field_validator, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_experiment_spec import Digest, StrictV2Contract

AES_256_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12
AES_GCM_TAG_BYTES = 16
HELDOUT_CASE_COUNT = 3
MAX_HELDOUT_ARCHIVE_BYTES = 16 * 1024 * 1024

OpaqueCaseId = Annotated[StrictStr, Field(pattern=r"^case-[0-9a-f]{32}$")]
NonceHex = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{24}$")]
CandidateFreezeId = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,255}$")]


class HeldoutSealError(ContractError):
    """A sealed held-out artifact was malformed, inconsistent, or unauthentic."""


class HeldoutArchiveCase(StrictV2Contract):
    """One generic repair Case; its content exists only inside the sealed archive."""

    case_id: OpaqueCaseId
    task: dict[str, JsonValue]
    workspace: dict[str, JsonValue]
    evaluation: dict[str, JsonValue]

    @field_validator("task", "workspace", "evaluation")
    @classmethod
    def require_non_empty_section(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if not value:
            raise ValueError("held-out Case sections must be non-empty")
        return value


class HeldoutArchive(StrictV2Contract):
    schema_version: Literal["cernora.reference.heldout-archive/v1"]
    cases: Annotated[tuple[HeldoutArchiveCase, ...], Field(min_length=3, max_length=3)]

    @field_validator("cases", mode="before")
    @classmethod
    def tuple_cases(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def ordered_unique_cases(self) -> Self:
        case_ids = tuple(case.case_id for case in self.cases)
        if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("held-out Case IDs must be sorted and unique")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @classmethod
    def from_bytes(cls, data: bytes) -> HeldoutArchive:
        if len(data) > MAX_HELDOUT_ARCHIVE_BYTES:
            raise HeldoutSealError("held-out archive exceeds the supported size")
        payload = load_json_bytes(data, maximum=MAX_HELDOUT_ARCHIVE_BYTES)
        if not isinstance(payload, dict):
            raise HeldoutSealError("held-out archive must be a JSON object")
        archive = cls.model_validate(payload)
        if archive.canonical_bytes() != data:
            raise HeldoutSealError("held-out archive is not canonical JSON")
        return archive


class HeldoutCaseCommitment(StrictV2Contract):
    case_id: OpaqueCaseId
    plaintext_sha256: Digest


class HeldoutManifest(StrictV2Contract):
    """Public pre-freeze commitment to exactly three encrypted held-out Cases."""

    schema_version: Literal["cernora.reference.heldout-manifest/v1"]
    manifest_id: Digest
    algorithm: Literal["AES-256-GCM"]
    case_commitments: Annotated[
        tuple[HeldoutCaseCommitment, ...], Field(min_length=3, max_length=3)
    ]
    suite_sha256: Digest
    archive_sha256: Digest
    ciphertext_sha256: Digest
    ciphertext_size: Annotated[StrictInt, Field(gt=AES_GCM_TAG_BYTES)]
    nonce: NonceHex
    aad_sha256: Digest

    @field_validator("case_commitments", mode="before")
    @classmethod
    def tuple_commitments(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def rederive_public_bindings(self) -> Self:
        case_ids = tuple(item.case_id for item in self.case_commitments)
        if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("manifest Case IDs must be sorted and unique")
        if self.ciphertext_size > MAX_HELDOUT_ARCHIVE_BYTES + AES_GCM_TAG_BYTES:
            raise ValueError("held-out ciphertext exceeds the supported size")
        if self.suite_sha256 != _suite_sha256(self.case_commitments):
            raise ValueError("held-out suite commitment is not canonical")
        if self.aad_sha256 != sha256_bytes(_aad_bytes(self)):
            raise ValueError("held-out AAD commitment is not canonical")
        payload = self.model_dump(mode="json", exclude={"manifest_id"})
        if self.manifest_id != canonical_content_id(payload, excluded=frozenset()):
            raise ValueError("held-out manifest identity is not canonical")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @classmethod
    def from_bytes(cls, data: bytes) -> HeldoutManifest:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise HeldoutSealError("held-out manifest must be a JSON object")
        manifest = cls.model_validate(payload)
        if manifest.canonical_bytes() != data:
            raise HeldoutSealError("held-out manifest is not canonical JSON")
        return manifest

    @classmethod
    def from_file(cls, path: Path) -> HeldoutManifest:
        return cls.from_bytes(read_regular_file_bytes(path))


class HeldoutRevealCaseRecord(StrictV2Contract):
    case_id: OpaqueCaseId
    sealed_plaintext_sha256: Digest
    revealed_authority_sha256: Digest
    task_authority_id: Digest
    task_authority_sha256: Digest

    @model_validator(mode="after")
    def authority_matches_commitment(self) -> Self:
        if self.sealed_plaintext_sha256 != self.revealed_authority_sha256:
            raise ValueError("revealed Case authority does not match its sealed commitment")
        return self


class HeldoutRevealReceipt(StrictV2Contract):
    """Post-freeze proof that one committed suite was revealed for one Candidate."""

    schema_version: Literal["cernora.reference.heldout-reveal-receipt/v1"]
    receipt_id: Digest
    manifest_id: Digest
    manifest_sha256: Digest
    candidate_freeze_id: CandidateFreezeId
    candidate_freeze_sha256: Digest
    revealed_archive_sha256: Digest
    case_records: Annotated[tuple[HeldoutRevealCaseRecord, ...], Field(min_length=3, max_length=3)]

    @field_validator("case_records", mode="before")
    @classmethod
    def tuple_case_records(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def rederive_receipt_bindings(self) -> Self:
        case_ids = tuple(item.case_id for item in self.case_records)
        if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("held-out reveal Case records must be sorted and unique")
        payload = self.model_dump(mode="json", exclude={"receipt_id"})
        if self.receipt_id != canonical_content_id(payload, excluded=frozenset()):
            raise ValueError("held-out reveal receipt identity is not canonical")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @classmethod
    def from_bytes(cls, data: bytes) -> HeldoutRevealReceipt:
        payload = load_json_bytes(data)
        if not isinstance(payload, dict):
            raise HeldoutSealError("held-out reveal receipt must be a JSON object")
        receipt = cls.model_validate(payload)
        if receipt.canonical_bytes() != data:
            raise HeldoutSealError("held-out reveal receipt is not canonical JSON")
        return receipt


def _case_commitments(archive: HeldoutArchive) -> tuple[HeldoutCaseCommitment, ...]:
    return tuple(
        HeldoutCaseCommitment(
            case_id=case.case_id,
            plaintext_sha256=sha256_bytes(canonical_json_bytes(case.model_dump(mode="json"))),
        )
        for case in archive.cases
    )


def _suite_sha256(commitments: Sequence[HeldoutCaseCommitment]) -> str:
    payload = {
        "schema_version": "cernora.reference.heldout-suite-commitment/v1",
        "cases": [item.model_dump(mode="json") for item in commitments],
    }
    return sha256_bytes(canonical_json_bytes(payload))


def _aad_payload(value: HeldoutManifest | Mapping[str, object]) -> dict[str, object]:
    if isinstance(value, HeldoutManifest):
        source: Mapping[str, object] = value.model_dump(mode="json")
    else:
        source = value
    return {
        "schema_version": "cernora.reference.heldout-seal-aad/v1",
        "algorithm": source["algorithm"],
        "case_commitments": source["case_commitments"],
        "suite_sha256": source["suite_sha256"],
        "archive_sha256": source["archive_sha256"],
        "nonce": source["nonce"],
    }


def _aad_bytes(value: HeldoutManifest | Mapping[str, object]) -> bytes:
    return canonical_json_bytes(_aad_payload(value))


def seal_heldout_cases(
    cases: Sequence[HeldoutArchiveCase | Mapping[str, object]],
    *,
    key: bytes,
    nonce: bytes,
) -> tuple[HeldoutManifest, bytes]:
    """Seal exactly three Cases with caller-supplied fresh secret material."""

    if len(key) != AES_256_KEY_BYTES:
        raise HeldoutSealError("AES-256-GCM requires a 32-byte key")
    if len(nonce) != AES_GCM_NONCE_BYTES:
        raise HeldoutSealError("AES-GCM requires a 12-byte nonce")
    try:
        archive = HeldoutArchive(
            schema_version="cernora.reference.heldout-archive/v1",
            cases=tuple(
                case
                if isinstance(case, HeldoutArchiveCase)
                else HeldoutArchiveCase.model_validate(case)
                for case in cases
            ),
        )
    except (TypeError, ValueError) as exc:
        raise HeldoutSealError("held-out Cases do not form a canonical archive") from exc
    archive_bytes = archive.canonical_bytes()
    if len(archive_bytes) > MAX_HELDOUT_ARCHIVE_BYTES:
        raise HeldoutSealError("held-out archive exceeds the supported size")
    commitments = _case_commitments(archive)
    partial: dict[str, object] = {
        "schema_version": "cernora.reference.heldout-manifest/v1",
        "algorithm": "AES-256-GCM",
        "case_commitments": [item.model_dump(mode="json") for item in commitments],
        "suite_sha256": _suite_sha256(commitments),
        "archive_sha256": sha256_bytes(archive_bytes),
        "nonce": nonce.hex(),
    }
    aad = _aad_bytes(partial)
    ciphertext = AESGCM(key).encrypt(nonce, archive_bytes, aad)
    partial["ciphertext_sha256"] = sha256_bytes(ciphertext)
    partial["ciphertext_size"] = len(ciphertext)
    partial["aad_sha256"] = sha256_bytes(aad)
    partial["manifest_id"] = canonical_content_id(partial, excluded=frozenset())
    return HeldoutManifest.model_validate(partial), ciphertext


def _verify_archive_commitments(
    manifest: HeldoutManifest,
    archive: HeldoutArchive,
) -> tuple[HeldoutCaseCommitment, ...]:
    archive_bytes = archive.canonical_bytes()
    if sha256_bytes(archive_bytes) != manifest.archive_sha256:
        raise HeldoutSealError("revealed archive digest does not match the manifest")
    commitments = _case_commitments(archive)
    if commitments != manifest.case_commitments:
        raise HeldoutSealError("revealed Case commitments do not match the manifest")
    if _suite_sha256(commitments) != manifest.suite_sha256:
        raise HeldoutSealError("revealed suite commitment does not match the manifest")
    return commitments


def materialize_reveal_receipt(
    manifest: HeldoutManifest,
    archive: HeldoutArchive,
    *,
    candidate_freeze_id: str,
    candidate_freeze_sha256: str,
) -> HeldoutRevealReceipt:
    """Record a verified reveal without disclosing the decryption key."""

    from cernora_reference_workflow.controlled_task import task_from_revealed_case

    commitments = _verify_archive_commitments(manifest, archive)
    tasks = tuple(task_from_revealed_case(case) for case in archive.cases)
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.heldout-reveal-receipt/v1",
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": sha256_bytes(manifest.canonical_bytes()),
        "candidate_freeze_id": candidate_freeze_id,
        "candidate_freeze_sha256": candidate_freeze_sha256,
        "revealed_archive_sha256": manifest.archive_sha256,
        "case_records": [
            {
                "case_id": item.case_id,
                "sealed_plaintext_sha256": item.plaintext_sha256,
                "revealed_authority_sha256": item.plaintext_sha256,
                "task_authority_id": task.authority_id,
                "task_authority_sha256": task.authority_sha256,
            }
            for item, task in zip(commitments, tasks, strict=True)
        ],
    }
    payload["receipt_id"] = canonical_content_id(payload, excluded=frozenset())
    try:
        return HeldoutRevealReceipt.model_validate(payload)
    except ValueError as exc:
        raise HeldoutSealError("CandidateFreeze binding is not a valid content identity") from exc


def reveal_heldout_archive(
    manifest: HeldoutManifest,
    ciphertext: bytes,
    *,
    key: bytes,
    candidate_freeze_id: str,
    candidate_freeze_sha256: str,
) -> tuple[HeldoutArchive, HeldoutRevealReceipt]:
    """Authenticate the seal, then record its identity-bound post-freeze reveal."""

    if len(key) != AES_256_KEY_BYTES:
        raise HeldoutSealError("held-out reveal key must be exactly 32 bytes")
    if len(ciphertext) != manifest.ciphertext_size:
        raise HeldoutSealError("held-out ciphertext size does not match the manifest")
    if sha256_bytes(ciphertext) != manifest.ciphertext_sha256:
        raise HeldoutSealError("held-out ciphertext digest does not match the manifest")
    try:
        archive_bytes = AESGCM(key).decrypt(
            bytes.fromhex(manifest.nonce), ciphertext, _aad_bytes(manifest)
        )
    except InvalidTag as exc:
        raise HeldoutSealError("held-out ciphertext authentication failed") from exc
    try:
        archive = HeldoutArchive.from_bytes(archive_bytes)
    except (ValueError, ContractError) as exc:
        raise HeldoutSealError("revealed held-out archive is invalid") from exc
    receipt = materialize_reveal_receipt(
        manifest,
        archive,
        candidate_freeze_id=candidate_freeze_id,
        candidate_freeze_sha256=candidate_freeze_sha256,
    )
    return archive, receipt


def verify_revealed_archive(
    manifest: HeldoutManifest,
    archive_bytes: bytes,
    receipt: HeldoutRevealReceipt,
    *,
    expected_candidate_freeze_id: str,
    expected_candidate_freeze_sha256: str,
) -> HeldoutArchive:
    """Verify an already revealed archive using only public offline evidence."""

    if receipt.manifest_id != manifest.manifest_id or (
        receipt.manifest_sha256 != sha256_bytes(manifest.canonical_bytes())
    ):
        raise HeldoutSealError("reveal receipt does not bind the supplied manifest")
    if (
        receipt.candidate_freeze_id != expected_candidate_freeze_id
        or receipt.candidate_freeze_sha256 != expected_candidate_freeze_sha256
    ):
        raise HeldoutSealError("reveal receipt does not bind the frozen Candidate")
    try:
        archive = HeldoutArchive.from_bytes(archive_bytes)
    except (ValueError, ContractError) as exc:
        raise HeldoutSealError("revealed held-out archive is invalid") from exc
    commitments = _verify_archive_commitments(manifest, archive)
    from cernora_reference_workflow.controlled_task import task_from_revealed_case

    tasks = tuple(task_from_revealed_case(case) for case in archive.cases)
    expected_records = tuple(
        HeldoutRevealCaseRecord(
            case_id=item.case_id,
            sealed_plaintext_sha256=item.plaintext_sha256,
            revealed_authority_sha256=item.plaintext_sha256,
            task_authority_id=task.authority_id,
            task_authority_sha256=task.authority_sha256,
        )
        for item, task in zip(commitments, tasks, strict=True)
    )
    if receipt.revealed_archive_sha256 != manifest.archive_sha256 or (
        receipt.case_records != expected_records
    ):
        raise HeldoutSealError("reveal receipt does not bind the revealed authorities")
    return archive
