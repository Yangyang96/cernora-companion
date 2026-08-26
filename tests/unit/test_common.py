from __future__ import annotations

import os
from pathlib import Path

import pytest

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_file,
    sha256_installed_code,
    validate_relative_path,
)


def test_canonical_json_is_stable_and_minimal() -> None:
    assert canonical_json_bytes({"z": 1, "a": "é"}) == b'{"a":"\xc3\xa9","z":1}'


@pytest.mark.parametrize(
    "payload",
    (b'{"a":1,"a":2}', b'{"n":NaN}', b"\xff", b"{"),
)
def test_strict_json_rejects_ambiguous_or_invalid_input(payload: bytes) -> None:
    with pytest.raises(ContractError):
        load_json_bytes(payload)


@pytest.mark.parametrize(
    "path",
    ("", "/absolute", "../escape", "dir/../escape", "dir\\file", "./file", "dir//file"),
)
def test_portable_paths_reject_unsafe_forms(path: str) -> None:
    with pytest.raises(ContractError):
        validate_relative_path(path)


def test_closed_tree_rejects_symlinks_and_hardlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target.txt"
    target.write_text("x", encoding="utf-8")
    symlink = root / "linked.txt"
    symlink.symlink_to(target)
    with pytest.raises(ContractError):
        closed_regular_tree(root)
    symlink.unlink()
    os.link(target, root / "hardlink.txt")
    with pytest.raises(ContractError):
        closed_regular_tree(root)


def test_installed_code_hash_allows_package_manager_hardlink(tmp_path: Path) -> None:
    source = tmp_path / "cache-module.py"
    installed = tmp_path / "installed-module.py"
    source.write_bytes(b"VALUE = 1\n")
    os.link(source, installed)

    with pytest.raises(ContractError, match="ambiguous hard links"):
        sha256_file(installed)
    assert sha256_installed_code(installed) == sha256_installed_code(source)


def test_installed_code_hash_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    linked = tmp_path / "linked.py"
    source.write_bytes(b"VALUE = 1\n")
    linked.symlink_to(source)

    with pytest.raises(ContractError, match="not an ordinary file"):
        sha256_installed_code(linked)


def test_stable_file_snapshot_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    linked = tmp_path / "linked.json"
    source.write_bytes(b"{}")
    linked.symlink_to(source)

    with pytest.raises(ContractError, match="ordinary file"):
        read_regular_file_bytes(linked)


@pytest.mark.parametrize("replacement", ("a-to-b", "aba"))
def test_stable_file_snapshot_rejects_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    target = tmp_path / "authority.json"
    candidate = tmp_path / "candidate.json"
    original = tmp_path / "original.json"
    displaced = tmp_path / "displaced.json"
    target.write_bytes(b'{"authority":"a"}')
    candidate.write_bytes(b'{"authority":"b"}')
    real_open = os.open
    replaced = False

    def replace_before_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        if not replaced and Path(path) == target:
            replaced = True
            os.replace(target, original)
            os.replace(candidate, target)
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if replacement == "aba":
                os.replace(target, displaced)
                os.replace(original, target)
            return descriptor
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_before_open)
    with pytest.raises(ContractError, match="changed before snapshot"):
        read_regular_file_bytes(target)


def test_stable_file_snapshot_is_size_bounded(tmp_path: Path) -> None:
    target = tmp_path / "oversized.json"
    target.write_bytes(b"{}" * 9)

    with pytest.raises(ContractError, match="exceeds 16 bytes"):
        read_regular_file_bytes(target, maximum=16)
