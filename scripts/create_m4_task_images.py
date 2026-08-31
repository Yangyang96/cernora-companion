#!/usr/bin/env python3
"""Build reproducible authority-bound M4 task images without network access."""

from __future__ import annotations

import argparse
import contextlib
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from cernora_reference_workflow.common import (
    ContractError,
    closed_regular_tree,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority, load_visible_task
from cernora_reference_workflow.m4_final_plan import materialize_m4_image_authority_set

_PLATFORM = "linux/arm64"
_PRIMARY_TAG = "priority4-sanitized"
_REPRO_TAG = "priority4-sanitized-repro-check"
_SYSTEM_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ACCEPTED_M4_RUNTIME_BASE = (
    "cernora-reference/codex-runtime@sha256:"
    "0e9ac928b97a83c54663f1086576039175b9bd4513b7d8d97f8173af62416788"
)
ACCEPTED_BUILDX_SHA256 = "04f8b9356a9275de46e86d2f5848c7698467e39a71394323fedd4e22eb045c1e"


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


def _resolve_docker_host(environ: Mapping[str, str]) -> str:
    selected = environ.get("CERNORA_DOCKER_HOST") or environ.get("DOCKER_HOST")
    candidates: tuple[Path, ...]
    if selected is not None:
        if not selected.startswith("unix://") or "\n" in selected or "\r" in selected:
            raise ContractError("M4 image build requires one local Unix Docker socket")
        candidates = (Path(selected.removeprefix("unix://")),)
    else:
        candidates = (
            Path("/var/run/docker.sock"),
            Path.home() / ".colima" / "default" / "docker.sock",
        )
    for candidate in candidates:
        try:
            metadata = candidate.stat()
        except OSError:
            continue
        if stat.S_ISSOCK(metadata.st_mode):
            return f"unix://{candidate.resolve()}"
    raise ContractError("M4 image build could not resolve one local Unix Docker socket")


def _public_child_environment(
    *, docker_host: str, client_home: Path, docker_config: Path
) -> dict[str, str]:
    return {
        "DOCKER_CONFIG": str(docker_config),
        "DOCKER_HOST": docker_host,
        "HOME": str(client_home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": _SYSTEM_PATH,
        "SOURCE_DATE_EPOCH": "0",
    }


def _task(path: Path) -> ControlledTaskAuthority:
    return ControlledTaskAuthority.from_bytes(read_regular_file_bytes(path))


def _load_tasks(
    visible_root: Path, heldout_task_paths: tuple[Path, ...]
) -> tuple[ControlledTaskAuthority, ...]:
    if not visible_root.is_dir() or visible_root.is_symlink():
        raise ContractError("visible corpus root must be one real directory")
    visible_roots = tuple(
        sorted(path for path in visible_root.iterdir() if path.is_dir() and not path.is_symlink())
    )
    visible = tuple(load_visible_task(path) for path in visible_roots)
    heldout = tuple(_task(path) for path in heldout_task_paths)
    tasks = tuple(sorted((*visible, *heldout), key=lambda item: item.case.case_id))
    if (
        len(visible) != 6
        or len(heldout) != 3
        or len(tasks) != 9
        or len({item.case.case_id for item in tasks}) != 9
        or {item.split_id for item in visible} != {"development", "regression"}
        or {item.split_id for item in heldout} != {"held-out"}
        or sum(item.split_id == "development" for item in visible) != 3
        or sum(item.split_id == "regression" for item in visible) != 3
    ):
        raise ContractError("M4 image build requires exact 3/3/3 task authorities")
    return tasks


def _heldout_task_paths(root: Path) -> tuple[Path, ...]:
    if not root.is_dir() or root.is_symlink():
        raise ContractError("held-out task root must be one real directory")
    files = closed_regular_tree(root)
    entries = tuple(root.iterdir())
    if (
        len(files) != 3
        or len(entries) != 3
        or any("/" in relative or not relative.endswith(".json") for relative in files)
        or any(entry.name not in files or entry.is_symlink() for entry in entries)
    ):
        raise ContractError("held-out task root must contain exactly three JSON authorities")
    return tuple(files[relative] for relative in sorted(files))


def _dockerfile(base_image: str) -> bytes:
    return (
        f"FROM {base_image}\nCOPY --chown=0:0 workspace/ /workspace/\nWORKDIR /workspace\n"
    ).encode()


def _write_context(task: ControlledTaskAuthority, context: Path, base_image: str) -> None:
    workspace = context / "workspace"
    workspace.mkdir(parents=True)
    for item in task.workspace_files:
        destination = workspace.joinpath(*item.path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(item.content())
    dockerfile = context / "Dockerfile"
    dockerfile.write_bytes(_dockerfile(base_image))
    for path in (dockerfile, *workspace.rglob("*"), workspace, context):
        os.utime(path, (0, 0), follow_symlinks=False)


def _run(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=environment,
    )


def _inspect_image(
    reference: str,
    *,
    environment: Mapping[str, str],
) -> tuple[str, str]:
    completed = _run(
        (
            "docker",
            "image",
            "inspect",
            reference,
            "--format",
            "{{.Id}} {{.Os}}/{{.Architecture}}",
        ),
        environment=environment,
    )
    values = completed.stdout.strip().split()
    if completed.returncode != 0 or len(values) != 2 or not values[0].startswith("sha256:"):
        raise ContractError("Docker image identity inspection failed")
    return values[0], values[1]


def _build(
    *,
    context: Path,
    tag: str,
    environment: Mapping[str, str],
) -> str:
    completed = _run(
        (
            "docker",
            "buildx",
            "build",
            "--load",
            "--network=none",
            "--no-cache",
            "--platform",
            _PLATFORM,
            "--provenance=false",
            "--sbom=false",
            "--tag",
            tag,
            ".",
        ),
        environment=environment,
        cwd=context,
    )
    if completed.returncode != 0:
        raise ContractError("network-disabled M4 task image build failed")
    image_id, platform = _inspect_image(tag, environment=environment)
    if platform != _PLATFORM:
        raise ContractError("M4 task image platform mismatch")
    return image_id


def _verify_workspace(
    image_id: str,
    task: ControlledTaskAuthority,
    *,
    work_root: Path,
    environment: Mapping[str, str],
) -> None:
    created = _run(
        ("docker", "create", "--entrypoint", "/bin/true", image_id),
        environment=environment,
    )
    container_id = created.stdout.strip()
    if created.returncode != 0 or not container_id:
        raise ContractError("M4 task image workspace probe could not create a container")
    probe = Path(tempfile.mkdtemp(prefix="cernora-m4-image-probe-", dir=work_root))
    try:
        copied = _run(
            ("docker", "cp", f"{container_id}:/workspace/.", str(probe)),
            environment=environment,
        )
        if copied.returncode != 0:
            raise ContractError("M4 task image workspace probe failed")
        observed = {
            relative: read_regular_file_bytes(path)
            for relative, path in closed_regular_tree(probe).items()
        }
        expected = {item.path: item.content() for item in task.workspace_files}
        if observed != expected:
            raise ContractError("M4 task image workspace contradicts task authority")
    finally:
        _run(("docker", "rm", "-f", container_id), environment=environment)
        shutil.rmtree(probe, ignore_errors=True)
    survived = _run(("docker", "container", "inspect", container_id), environment=environment)
    if survived.returncode == 0:
        raise ContractError("M4 task image probe container survived cleanup")


def _remove_tag(tag: str, *, environment: Mapping[str, str]) -> None:
    completed = _run(("docker", "image", "rm", tag), environment=environment)
    if completed.returncode != 0:
        raise ContractError("temporary reproducibility-check image tag survived cleanup")


def _exclusive_publish(path: Path, content: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        offset = 0
        while offset < len(content):
            offset += os.write(descriptor, content[offset:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    finally:
        with contextlib.suppress(OSError):
            os.close(descriptor)


@contextlib.contextmanager
def _isolated_docker_environment(work_root: Path) -> Iterator[dict[str, str]]:
    client_root = Path(tempfile.mkdtemp(prefix="cernora-m4-docker-client-", dir=work_root))
    client_home = client_root / "home"
    docker_config = client_root / "docker-config"
    client_home.mkdir(mode=0o700)
    docker_config.mkdir(mode=0o700)
    try:
        plugin_source = (
            Path(pwd.getpwuid(os.getuid()).pw_dir) / ".docker" / "cli-plugins" / "docker-buildx"
        )
        plugin = read_regular_file_bytes(plugin_source, maximum=None)
        if sha256_bytes(plugin) != ACCEPTED_BUILDX_SHA256:
            raise ContractError("installed Buildx plugin does not match accepted authority")
        plugin_root = docker_config / "cli-plugins"
        plugin_root.mkdir(mode=0o700)
        installed_plugin = plugin_root / "docker-buildx"
        installed_plugin.write_bytes(plugin)
        installed_plugin.chmod(0o700)
        yield _public_child_environment(
            docker_host=_resolve_docker_host(os.environ),
            client_home=client_home,
            docker_config=docker_config,
        )
    finally:
        shutil.rmtree(client_root, ignore_errors=True)


def _build_all_images(
    tasks: tuple[ControlledTaskAuthority, ...],
    *,
    build_base_image: str,
    work_root: Path,
    environment: Mapping[str, str],
    image_prefix: str = "cernora-reference/m4-",
) -> dict[str, str]:
    base_id, base_platform = _inspect_image(build_base_image, environment=environment)
    if build_base_image.rsplit("@", 1)[-1] != base_id or base_platform != _PLATFORM:
        raise ContractError("M4 build base image identity or platform mismatch")
    image_references: dict[str, str] = {}
    for task in tasks:
        case_id = task.case.case_id
        primary_tag = f"{image_prefix}{case_id}:{_PRIMARY_TAG}"
        repro_tag = f"{image_prefix}{case_id}:{_REPRO_TAG}"
        if (
            _run(("docker", "image", "inspect", primary_tag), environment=environment).returncode
            == 0
        ):
            raise ContractError("M4 primary image tag already exists")
        if _run(("docker", "image", "inspect", repro_tag), environment=environment).returncode == 0:
            raise ContractError("M4 reproducibility image tag already exists")
        context = Path(tempfile.mkdtemp(prefix=f"cernora-m4-image-{case_id}-", dir=work_root))
        try:
            _write_context(task, context, build_base_image)
            first = _build(context=context, tag=primary_tag, environment=environment)
            _verify_workspace(first, task, work_root=work_root, environment=environment)
            second = _build(context=context, tag=repro_tag, environment=environment)
            _verify_workspace(second, task, work_root=work_root, environment=environment)
            _remove_tag(repro_tag, environment=environment)
            if first != second:
                raise ContractError("M4 task image is not reproducible across no-cache builds")
            digest = first.removeprefix("sha256:")
            image_references[case_id] = f"{image_prefix}{case_id}@sha256:{digest}"
        finally:
            shutil.rmtree(context, ignore_errors=True)
    return image_references


def create_m4_task_images(
    *,
    visible_root: Path,
    heldout_task_paths: tuple[Path, ...],
    build_base_image: str,
    work_root: Path,
    output: Path,
) -> None:
    """Build every image twice and publish only stable content identities."""

    tasks = _load_tasks(visible_root, heldout_task_paths)
    if not work_root.is_dir() or work_root.is_symlink() or _inside_git_worktree(work_root):
        raise ContractError("M4 image work root must be one Git-external real directory")
    if output.exists() or output.is_symlink() or _inside_git_worktree(output):
        raise ContractError("M4 image authority output must be new and outside every worktree")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ContractError("M4 image authority output parent must be one real directory")
    if build_base_image != ACCEPTED_M4_RUNTIME_BASE:
        raise ContractError("M4 image build requires the accepted Runtime base authority")
    with _isolated_docker_environment(work_root) as environment:
        image_references = _build_all_images(
            tasks,
            build_base_image=build_base_image,
            work_root=work_root,
            environment=environment,
        )
    authorities = materialize_m4_image_authority_set(
        build_base_image=build_base_image,
        images=image_references,
    )
    _exclusive_publish(output, authorities.canonical_bytes())
    if read_regular_file_bytes(output) != authorities.canonical_bytes():
        raise ContractError("published M4 image authorities changed after publication")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visible-root", type=Path, required=True)
    parser.add_argument("--heldout-task-root", type=Path, required=True)
    parser.add_argument("--build-base-image", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        create_m4_task_images(
            visible_root=arguments.visible_root,
            heldout_task_paths=_heldout_task_paths(arguments.heldout_task_root),
            build_base_image=arguments.build_base_image,
            work_root=arguments.work_root,
            output=arguments.output,
        )
    except (ContractError, OSError, ValueError):
        print("error: M4 task image creation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
