#!/usr/bin/env python3
"""Reveal the sealed P4 held-out Cases under one explicit user authorization.

Boundary-R tool: decrypts the sealed commitment with the custodian's key,
revalidates every commitment against the revealed content, binds the reveal
receipt to the frozen Candidate Development record, and emits exactly three
private task-authority JSON files. It refuses to run without the
authorization flag, places the key and outputs outside every Git worktree,
never prints Case content, and is one-shot per output directory.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

from cernora_reference_workflow.candidate_development import CandidateDevelopmentRecord
from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    reconstructed_revealed_case,
    task_from_revealed_case,
)
from cernora_reference_workflow.heldout_seal import (
    AES_256_KEY_BYTES,
    HeldoutManifest,
    HeldoutSealError,
    reveal_heldout_archive,
)

FileIdentity = tuple[int, int]


def _path_is_inside_git_worktree(path: Path) -> bool:
    resolved = path.resolve()
    start = resolved if resolved.is_dir() else resolved.parent
    for parent in (start, *start.parents):
        marker = parent / ".git"
        try:
            metadata = marker.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode):
            return True
    return False


def _require_private_path(path: Path, *, label: str) -> Path:
    resolved = path.resolve()
    if _path_is_inside_git_worktree(resolved):
        raise ContractError(f"{label} must be outside every Git worktree")
    return resolved


def _unlink_owned_file(path: Path, identity: FileIdentity) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISREG(metadata.st_mode) and (metadata.st_dev, metadata.st_ino) == identity:
        path.unlink()


def _exclusive_write(path: Path, payload: bytes, *, mode: int) -> FileIdentity:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    opened = os.fstat(descriptor)
    identity = (opened.st_dev, opened.st_ino)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        _unlink_owned_file(path, identity)
        raise
    finally:
        with contextlib.suppress(OSError):
            os.close(descriptor)
    return identity


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_prior_record(path: Path) -> CandidateDevelopmentRecord:
    data = read_regular_file_bytes(path)
    payload = load_json_bytes(data)
    if not isinstance(payload, dict):
        raise ContractError("Candidate Development record must be a JSON object")
    record = CandidateDevelopmentRecord.model_validate(payload)
    if canonical_json_bytes(record.model_dump(mode="json")) != data:
        raise ContractError("Candidate Development record is not canonical JSON")
    return record


def _load_manifest(path: Path) -> HeldoutManifest:
    return HeldoutManifest.from_file(path)


def _verify_commitments(
    manifest: HeldoutManifest,
    tasks: tuple[ControlledTaskAuthority, ...],
) -> None:
    commitment_by_case = {item.case_id: item for item in manifest.case_commitments}
    task_case_ids = tuple(task.case.case_id for task in tasks)
    if (
        task_case_ids != tuple(sorted(task_case_ids))
        or len(task_case_ids) != len(set(task_case_ids))
        or set(task_case_ids) != set(commitment_by_case)
    ):
        raise ContractError("revealed Cases do not match the sealed commitments")
    for task in tasks:
        commitment = commitment_by_case[task.case.case_id]
        reconstructed = reconstructed_revealed_case(task)
        revealed_digest = sha256_bytes(canonical_json_bytes(reconstructed.model_dump(mode="json")))
        if revealed_digest != commitment.plaintext_sha256:
            raise ContractError("revealed Case does not equal its sealed commitment")


def create_p4_heldout_reveal(
    *,
    manifest_path: Path,
    ciphertext_path: Path,
    key_path: Path,
    prior_record_path: Path,
    output_dir: Path,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Perform the one-shot authorized reveal and publish private authorities."""

    key_file = _require_private_path(key_path, label="held-out reveal key")
    private_root = _require_private_path(output_dir, label="held-out reveal output")
    if private_root.exists() or private_root.is_symlink():
        raise ContractError("held-out reveal output must not already exist")
    if not private_root.parent.is_dir() or private_root.parent.is_symlink():
        raise ContractError("held-out reveal output parent must be a real directory")

    manifest = _load_manifest(manifest_path)
    ciphertext = read_regular_file_bytes(ciphertext_path)
    if (
        len(ciphertext) != manifest.ciphertext_size
        or sha256_bytes(ciphertext) != manifest.ciphertext_sha256
    ):
        raise ContractError("held-out ciphertext does not match the manifest")
    key = read_regular_file_bytes(key_file, maximum=AES_256_KEY_BYTES)
    if len(key) != AES_256_KEY_BYTES:
        raise ContractError("held-out reveal key must be exactly 32 bytes")
    prior = _load_prior_record(prior_record_path)
    prior_bytes = canonical_json_bytes(prior.model_dump(mode="json"))

    archive, receipt = reveal_heldout_archive(
        manifest,
        ciphertext,
        key=key,
        candidate_freeze_id=prior.development_id,
        candidate_freeze_sha256=sha256_bytes(prior_bytes),
    )
    tasks = tuple(task_from_revealed_case(case) for case in archive.cases)
    _verify_commitments(manifest, tasks)

    private_root.mkdir(mode=0o700)
    owned: list[tuple[Path, FileIdentity]] = []
    try:
        for task in tasks:
            destination = private_root / f"{task.case.case_id}.json"
            owned.append(
                (
                    destination,
                    _exclusive_write(destination, task.canonical_bytes(), mode=0o600),
                )
            )
        receipt_path = private_root / "reveal-receipt.json"
        owned.append(
            (
                receipt_path,
                _exclusive_write(
                    receipt_path,
                    receipt.canonical_bytes(),
                    mode=0o600,
                ),
            )
        )
        _sync_directory(private_root)
    except BaseException:
        for target, identity in reversed(owned):
            _unlink_owned_file(target, identity)
        with contextlib.suppress(OSError):
            private_root.rmdir()
        raise
    published = tuple((task.case.case_id, task.authority_id) for task in tasks)
    return receipt.receipt_id, published


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ciphertext", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--prior-record", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--authorize-reveal",
        action="store_true",
        help="explicit user authorization for this one-shot held-out reveal",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    if not arguments.authorize_reveal:
        print("error: the reveal requires --authorize-reveal", file=sys.stderr)
        return 2
    try:
        receipt_id, published = create_p4_heldout_reveal(
            manifest_path=arguments.manifest,
            ciphertext_path=arguments.ciphertext,
            key_path=arguments.key,
            prior_record_path=arguments.prior_record,
            output_dir=arguments.output_dir,
        )
    except (ContractError, HeldoutSealError, OSError, ValueError, json.JSONDecodeError):
        print("error: held-out reveal failed", file=sys.stderr)
        return 1
    summary = {
        "receipt_id": receipt_id,
        "revealed": [
            {"case_id": case_id, "task_authority_id": authority_id}
            for case_id, authority_id in published
        ],
    }
    print(canonical_json_bytes(summary).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
