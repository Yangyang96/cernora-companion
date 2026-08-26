from __future__ import annotations

import os
from pathlib import Path

import pytest


def install_file_replacement(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: Path,
    candidate: Path,
    replacement: str,
) -> None:
    original = target.with_name(f"{target.name}.original")
    displaced = target.with_name(f"{target.name}.displaced")
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
        if not replaced and os.fspath(path) == os.fspath(target):
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
