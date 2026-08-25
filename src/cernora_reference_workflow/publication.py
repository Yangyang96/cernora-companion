"""Atomic directory publication without replacement on supported hosts."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path

from cernora_reference_workflow.common import ContractError

AT_FDCWD = -2 if sys.platform == "darwin" else -100
RENAME_EXCL = 0x00000004
RENAME_NOREPLACE = 0x00000001


class PublicationError(ContractError):
    """A closed directory could not be published safely."""


def _raise_rename_error(destination: Path) -> None:
    error_number = ctypes.get_errno()
    if error_number in (errno.EEXIST, errno.ENOTEMPTY):
        raise PublicationError(f"destination must not already exist: {destination.name}")
    raise PublicationError(f"atomic no-replace publication failed: {os.strerror(error_number)}")


def atomic_publish_directory(staging: Path, destination: Path) -> None:
    """Atomically rename one sibling directory while refusing any replacement."""

    if staging.parent.resolve() != destination.parent.resolve():
        raise PublicationError("staging and destination must share a parent directory")
    if not staging.is_dir() or staging.is_symlink():
        raise PublicationError("staging path must be a real directory")

    source_bytes = os.fsencode(staging)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    ctypes.set_errno(0)

    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(source_bytes, destination_bytes, RENAME_EXCL) != 0:
            _raise_rename_error(destination)
        return

    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if (
            rename(
                AT_FDCWD,
                source_bytes,
                AT_FDCWD,
                destination_bytes,
                RENAME_NOREPLACE,
            )
            != 0
        ):
            _raise_rename_error(destination)
        return

    raise PublicationError(f"atomic no-replace publication is unsupported on {sys.platform}")
