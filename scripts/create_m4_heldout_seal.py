"""Seal a private canonical held-out archive without printing secret material."""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import stat
from collections.abc import Sequence
from pathlib import Path

from cernora_reference_workflow.common import read_regular_file_bytes
from cernora_reference_workflow.heldout_seal import (
    AES_256_KEY_BYTES,
    AES_GCM_NONCE_BYTES,
    MAX_HELDOUT_ARCHIVE_BYTES,
    HeldoutArchive,
    seal_heldout_cases,
)

FileIdentity = tuple[int, int]


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


def _require_private_custody_path(path: Path, *, label: str) -> Path:
    resolved = path.resolve()
    if _path_is_inside_git_worktree(resolved):
        raise RuntimeError(f"{label} must be outside every Git worktree")
    return resolved


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--key-output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    archive_path = _require_private_custody_path(args.archive, label="held-out plaintext archive")
    key_output = _require_private_custody_path(args.key_output, label="held-out key output")
    archive = HeldoutArchive.from_bytes(
        read_regular_file_bytes(archive_path, maximum=MAX_HELDOUT_ARCHIVE_BYTES)
    )
    key = secrets.token_bytes(AES_256_KEY_BYTES)
    nonce = secrets.token_bytes(AES_GCM_NONCE_BYTES)
    manifest, ciphertext = seal_heldout_cases(archive.cases, key=key, nonce=nonce)

    args.output_dir.mkdir(mode=0o755)
    owned_artifacts: list[tuple[Path, FileIdentity]] = []
    key_identity: FileIdentity | None = None
    try:
        ciphertext_path = args.output_dir / "ciphertext.bin"
        owned_artifacts.append(
            (ciphertext_path, _exclusive_write(ciphertext_path, ciphertext, mode=0o644))
        )
        manifest_path = args.output_dir / "manifest.json"
        owned_artifacts.append(
            (
                manifest_path,
                _exclusive_write(manifest_path, manifest.canonical_bytes(), mode=0o644),
            )
        )
        key_identity = _exclusive_write(key_output, key, mode=0o600)
        _sync_directory(args.output_dir)
        _sync_directory(key_output.parent)
    except BaseException:
        if key_identity is not None:
            _unlink_owned_file(key_output, key_identity)
        for target, identity in reversed(owned_artifacts):
            _unlink_owned_file(target, identity)
        with contextlib.suppress(OSError):
            args.output_dir.rmdir()
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
