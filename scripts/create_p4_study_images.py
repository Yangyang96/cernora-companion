#!/usr/bin/env python3
"""Build the three revealed P4 held-out task images and the study image set.

Post-reveal offline tool: rebuilds each revealed held-out Case image twice
with the network-disabled no-cache procedure, verifies the container
workspace byte-equals the task authority, and publishes one merged
twelve-Case study image set (the nine frozen pilot images plus the three
new digests) as canonical JSON outside every Git worktree.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol, cast

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    validate_sha256,
)
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.spec_builder import BASE_IMAGE

STUDY_IMAGE_PREFIX = "cernora-reference/p4-study-"
STUDY_PRIMARY_TAG = "p4-study"
STUDY_REPRO_TAG = "p4-study-repro-check"
PILOT_IMAGES_SCHEMA = "cernora.reference.development-pilot-images/v2"
STUDY_IMAGES_SCHEMA = "cernora.reference.study-images/v1"
FileIdentity = tuple[int, int]


class _M4Images(Protocol):
    _PLATFORM: str

    def _isolated_docker_environment(
        self, work_root: Path
    ) -> AbstractContextManager[dict[str, str]]: ...

    def _build(self, *, context: Path, tag: str, environment: Mapping[str, str]) -> str: ...

    def _inspect_image(
        self, reference: str, *, environment: Mapping[str, str]
    ) -> tuple[str, str]: ...

    def _remove_tag(self, tag: str, *, environment: Mapping[str, str]) -> None: ...

    def _verify_workspace(
        self,
        image_id: str,
        task: ControlledTaskAuthority,
        *,
        work_root: Path,
        environment: Mapping[str, str],
    ) -> None: ...

    def _write_context(
        self, task: ControlledTaskAuthority, context: Path, base_image: str
    ) -> None: ...


def _load_m4_images_module() -> _M4Images:
    location = Path(__file__).resolve().parent / "create_m4_task_images.py"
    spec = importlib.util.spec_from_file_location("create_m4_task_images", location)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["create_m4_task_images"] = module
    spec.loader.exec_module(module)
    return cast("_M4Images", module)


def _inside_git_worktree(path: Path) -> bool:
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


def _is_reveal_layout(files: Mapping[str, Path]) -> bool:
    case_files = [name for name in files if name.startswith("case-") and name.endswith(".json")]
    return (
        "reveal-receipt.json" in files
        and len(case_files) == 3
        and len(files) == 4
        and all("/" not in name for name in files)
    )


def load_revealed_tasks(root: Path) -> tuple[ControlledTaskAuthority, ...]:
    """Three held-out task authorities plus their reveal receipt, or nothing."""

    if not root.is_dir() or root.is_symlink():
        raise ContractError("revealed task root must be one real directory")
    files = closed_regular_tree(root)
    if not _is_reveal_layout(files):
        raise ContractError("revealed task root must be one complete reveal output")
    tasks = tuple(
        ControlledTaskAuthority.from_bytes(read_regular_file_bytes(files[name]))
        for name in sorted(files)
        if name.startswith("case-")
    )
    case_ids = tuple(task.case.case_id for task in tasks)
    if (
        len(tasks) != 3
        or len(case_ids) != len(set(case_ids))
        or any(task.split_id != "held-out" for task in tasks)
    ):
        raise ContractError("revealed task root must contain three unique held-out cases")
    return tasks


def load_pilot_images(path: Path) -> tuple[dict[str, str], str]:
    """The nine frozen pilot image references plus the pinned base image."""

    payload = load_json_bytes(read_regular_file_bytes(path))
    if not isinstance(payload, dict) or payload.get("schema_version") != PILOT_IMAGES_SCHEMA:
        raise ContractError("pilot image set must use the frozen v2 schema")
    build_base = payload.get("build_base_image")
    images_member = payload.get("images")
    if (
        not isinstance(build_base, str)
        or build_base != BASE_IMAGE
        or not isinstance(images_member, list)
    ):
        raise ContractError("pilot image set must pin the accepted pi base image")
    images: dict[str, str] = {}
    for entry in images_member:
        if not isinstance(entry, dict) or set(entry) != {"case_id", "image"}:
            raise ContractError("pilot image entries must be case/image pairs")
        case_id, image = entry["case_id"], entry["image"]
        if not isinstance(case_id, str) or not isinstance(image, str):
            raise ContractError("pilot image entries must be strings")
        marker = "@sha256:"
        if marker not in image:
            raise ContractError("pilot image references must be digest-pinned")
        validate_sha256(image.rsplit(marker, 1)[1], label="pilot task image digest")
        images[case_id] = image
    if len(images) != 9 or not images:
        raise ContractError("pilot image set must cover the nine visible Cases")
    return images, build_base


def merge_study_images(
    pilot_images: Mapping[str, str],
    built_images: Mapping[str, str],
    build_base_image: str,
) -> dict[str, object]:
    """One canonical twelve-Case study image set over both sources."""

    if set(pilot_images) & set(built_images):
        raise ContractError("pilot and held-out image sets overlap")
    images = {**pilot_images, **built_images}
    if len(images) != 12:
        raise ContractError("study image set must cover exactly twelve Cases")
    payload: dict[str, object] = {
        "schema_version": STUDY_IMAGES_SCHEMA,
        "build_base_image": build_base_image,
        "platform": "linux/arm64",
        "images": [{"case_id": case_id, "image": images[case_id]} for case_id in sorted(images)],
    }
    payload["image_set_id"] = canonical_content_id(payload, excluded=frozenset())
    return payload


def build_heldout_images(
    tasks: tuple[ControlledTaskAuthority, ...],
    *,
    build_base_image: str,
    work_root: Path,
) -> dict[str, str]:
    """Dual-build every revealed task image and return digest-pinned references."""

    m4_images = _load_m4_images_module()
    with m4_images._isolated_docker_environment(work_root) as environment:
        base_id, base_platform = m4_images._inspect_image(build_base_image, environment=environment)
        if build_base_image.rsplit("@", 1)[-1] != base_id or base_platform != "linux/arm64":
            raise ContractError("study build base image identity or platform mismatch")
        references: dict[str, str] = {}
        for task in tasks:
            case_id = task.case.case_id
            primary_tag = f"{STUDY_IMAGE_PREFIX}{case_id}:{STUDY_PRIMARY_TAG}"
            repro_tag = f"{STUDY_IMAGE_PREFIX}{case_id}:{STUDY_REPRO_TAG}"
            context = Path(tempfile.mkdtemp(prefix=f"cernora-p4-image-{case_id}-", dir=work_root))
            try:
                m4_images._write_context(task, context, build_base_image)
                first = m4_images._build(context=context, tag=primary_tag, environment=environment)
                m4_images._verify_workspace(
                    first, task, work_root=work_root, environment=environment
                )
                second = m4_images._build(context=context, tag=repro_tag, environment=environment)
                m4_images._verify_workspace(
                    second, task, work_root=work_root, environment=environment
                )
                m4_images._remove_tag(repro_tag, environment=environment)
                if first != second:
                    raise ContractError("held-out task image is not reproducible")
                references[case_id] = (
                    f"{STUDY_IMAGE_PREFIX}{case_id}@sha256:{first.removeprefix('sha256:')}"
                )
            finally:
                shutil.rmtree(context, ignore_errors=True)
        return references


def _exclusive_write(path: Path, payload: bytes) -> FileIdentity:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
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
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    finally:
        with contextlib.suppress(OSError):
            os.close(descriptor)
    return identity


def create_p4_study_images(
    *,
    revealed_root: Path,
    pilot_images_path: Path,
    build_base_image: str,
    work_root: Path,
    output: Path,
) -> dict[str, object]:
    """Build the revealed images twice and publish the merged study set."""

    tasks = load_revealed_tasks(revealed_root)
    pilot_images, pilot_base = load_pilot_images(pilot_images_path)
    if build_base_image != BASE_IMAGE or pilot_base != BASE_IMAGE:
        raise ContractError("study image build requires the accepted pi runtime base image")
    if not work_root.is_dir() or work_root.is_symlink() or _inside_git_worktree(work_root):
        raise ContractError("study image work root must be a Git-external real directory")
    if output.exists() or output.is_symlink() or _inside_git_worktree(output):
        raise ContractError("study image output must be new and outside every worktree")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ContractError("study image output parent must be a real directory")
    built = build_heldout_images(
        tasks,
        build_base_image=build_base_image,
        work_root=work_root,
    )
    payload = merge_study_images(pilot_images, built, build_base_image)
    _exclusive_write(output, canonical_json_bytes(payload))
    if read_regular_file_bytes(output) != canonical_json_bytes(payload):
        raise ContractError("published study image set changed after publication")
    return payload


def _parse_args() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revealed-root", type=Path, required=True)
    parser.add_argument("--pilot-images", type=Path, required=True)
    parser.add_argument("--build-base-image", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parse_args().parse_args()
    try:
        payload = create_p4_study_images(
            revealed_root=arguments.revealed_root,
            pilot_images_path=arguments.pilot_images,
            build_base_image=arguments.build_base_image,
            work_root=arguments.work_root,
            output=arguments.output,
        )
    except (ContractError, OSError, ValueError):
        print("error: study image creation failed", file=sys.stderr)
        return 1
    images_member = payload["images"]
    assert isinstance(images_member, list)
    summary = {
        "image_set_id": payload["image_set_id"],
        "case_count": len(images_member),
    }
    print(canonical_json_bytes(summary).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
