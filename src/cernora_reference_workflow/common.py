"""Canonical JSON, content identity, and safe portable-path primitives."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

SHA256_LENGTH = 64
MAX_JSON_BYTES = 16 * 1024 * 1024


class ContractError(ValueError):
    """A strict portable contract was malformed or unverifiable."""


def _reject_constant(value: str) -> NoReturn:
    raise ContractError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_json_bytes(data: bytes, *, maximum: int = MAX_JSON_BYTES) -> Any:
    """Decode strict UTF-8 JSON with duplicate and non-finite number rejection."""

    if len(data) > maximum:
        raise ContractError(f"JSON input exceeds {maximum} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("JSON input is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON: {exc.msg}") from exc


def load_json_file(path: Path, *, maximum: int = MAX_JSON_BYTES) -> Any:
    return load_json_bytes(read_regular_file_bytes(path, maximum=maximum), maximum=maximum)


def _metadata(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def read_regular_file_bytes(path: Path, *, maximum: int | None = MAX_JSON_BYTES) -> bytes:
    """Read one no-follow ordinary file through a metadata-stable descriptor."""

    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ContractError(f"path is not an unambiguous ordinary file: {path.name}")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _metadata(before) != _metadata(opened):
                raise ContractError(f"file changed before snapshot: {path.name}")
            payload = handle.read() if maximum is None else handle.read(maximum + 1)
            finished = os.fstat(handle.fileno())
        after = path.lstat()
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError(f"cannot snapshot required file: {path.name}") from exc
    if _metadata(opened) != _metadata(finished) or _metadata(opened) != _metadata(after):
        raise ContractError(f"file changed during snapshot: {path.name}")
    if maximum is not None and len(payload) > maximum:
        raise ContractError(f"file exceeds {maximum} bytes: {path.name}")
    return payload


def canonical_json_bytes(value: Any) -> bytes:
    """Return the experiment's deterministic UTF-8 canonical JSON representation."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError("value is not canonical JSON data") from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    require_regular_file(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_installed_code(path: Path) -> str:
    """Hash an installed ordinary file while permitting wheel-cache hard links.

    Evidence trees use :func:`sha256_file` and continue to reject every hard link. Installed
    wheels are a different boundary: package managers commonly hard-link immutable cache bytes
    into an environment. This helper rejects symlinks, non-files, path replacement, and mutation
    during the read without treating that standard installation layout as malformed evidence.
    """

    try:
        path_metadata = path.lstat()
    except OSError as exc:
        raise ContractError(f"cannot inspect installed code file: {path.name}") from exc
    if not stat.S_ISREG(path_metadata.st_mode):
        raise ContractError(f"installed code path is not an ordinary file: {path.name}")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ContractError(f"cannot open installed code file: {path.name}") from exc
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (path_metadata.st_dev, path_metadata.st_ino):
            raise ContractError(f"installed code path changed before hashing: {path.name}")
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        finished = os.fstat(handle.fileno())
    if (opened.st_size, opened.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns):
        raise ContractError(f"installed code file changed while hashing: {path.name}")
    return digest.hexdigest()


def canonical_content_id(value: Mapping[str, Any], *, excluded: frozenset[str]) -> str:
    return sha256_bytes(canonical_json_bytes({k: v for k, v in value.items() if k not in excluded}))


def validate_sha256(value: str, *, label: str = "sha256") -> str:
    if len(value) != SHA256_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise ContractError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def validate_relative_path(value: str) -> str:
    """Require one canonical, slash-separated, contained portable path."""

    if not value or "\\" in value or "\x00" in value:
        raise ContractError("path must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        raise ContractError(f"path is not canonical and relative: {value!r}")
    if any(part in ("", ".", "..") for part in path.parts):
        raise ContractError(f"path contains an unsafe segment: {value!r}")
    return value


def require_regular_file(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ContractError(f"cannot inspect required file: {path.name}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ContractError(f"path is not an ordinary file: {path.name}")
    if metadata.st_nlink != 1:
        raise ContractError(f"file has ambiguous hard links: {path.name}")
    return metadata


def closed_regular_tree(root: Path) -> dict[str, Path]:
    """Return a closed ordinary-file tree without following unsafe entries."""

    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise ContractError("completed-export root is unavailable") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
        raise ContractError("completed-export root must be a real directory")

    files: dict[str, Path] = {}
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(dirnames):
            child = directory_path / name
            metadata = child.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or child.is_symlink():
                raise ContractError(f"unsafe directory entry: {child.relative_to(root)}")
        for name in sorted(filenames):
            child = directory_path / name
            require_regular_file(child)
            relative = child.relative_to(root).as_posix()
            validate_relative_path(relative)
            files[relative] = child
    return dict(sorted(files.items()))
