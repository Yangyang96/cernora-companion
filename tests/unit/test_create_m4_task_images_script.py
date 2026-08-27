from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

from cernora_reference_workflow.common import (
    ContractError,
    closed_regular_tree,
    read_regular_file_bytes,
)
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority, load_visible_task


class _Script(Protocol):
    ACCEPTED_M4_RUNTIME_BASE: str

    def _dockerfile(self, base_image: str) -> bytes: ...

    def _inside_git_worktree(self, path: Path) -> bool: ...

    def _load_tasks(
        self, visible_root: Path, heldout_task_paths: tuple[Path, ...]
    ) -> tuple[ControlledTaskAuthority, ...]: ...

    def _heldout_task_paths(self, root: Path) -> tuple[Path, ...]: ...

    def _public_child_environment(
        self, *, docker_host: str, client_home: Path, docker_config: Path
    ) -> dict[str, str]: ...

    def _write_context(
        self,
        task: ControlledTaskAuthority,
        context: Path,
        base_image: str,
    ) -> None: ...


class _PlanScript(Protocol):
    def _heldout_task_paths(self, root: Path) -> tuple[Path, ...]: ...


def _script() -> tuple[_Script, ModuleType]:
    path = Path(__file__).resolve().parents[2] / "scripts/create_m4_task_images.py"
    specification = importlib.util.spec_from_file_location("create_m4_task_images", path)
    if specification is None or specification.loader is None:
        raise AssertionError("could not load M4 task image script")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return cast(_Script, module), module


def _plan_script() -> _PlanScript:
    path = Path(__file__).resolve().parents[2] / "scripts/create_m4_final_plans.py"
    specification = importlib.util.spec_from_file_location("create_m4_final_plans", path)
    if specification is None or specification.loader is None:
        raise AssertionError("could not load M4 final-plan script")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return cast(_PlanScript, module)


def test_image_context_contains_only_authorized_workspace(tmp_path: Path) -> None:
    script, _ = _script()
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    context = tmp_path / "context"
    context.mkdir()

    script._write_context(task, context, script.ACCEPTED_M4_RUNTIME_BASE)

    tree = closed_regular_tree(context)
    assert set(tree) == {"Dockerfile", *[f"workspace/{item.path}" for item in task.workspace_files]}
    assert read_regular_file_bytes(tree["Dockerfile"]) == script._dockerfile(
        script.ACCEPTED_M4_RUNTIME_BASE
    )
    assert {
        relative.removeprefix("workspace/"): read_regular_file_bytes(path)
        for relative, path in tree.items()
        if relative.startswith("workspace/")
    } == {item.path: item.content() for item in task.workspace_files}
    assert all(path.stat().st_mtime == 0 for path in tree.values())


def test_image_build_environment_drops_private_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script, _ = _script()
    inherited = {
        "HOME": "/personal/home",
        "DOCKER_CONFIG": "/personal/docker-config",
        "AWS_ACCESS_KEY_ID": "private-access-key",
        "XDG_CONFIG_HOME": "/personal/xdg",
        "HTTP_PROXY": "private-proxy",
        "CUSTOM_AUTH_FILE": "private-auth",
        "ACCESS_TOKEN": "private-token",
    }
    monkeypatch.setattr(os, "environ", inherited)
    client_home = tmp_path / "empty-home"
    docker_config = tmp_path / "empty-docker-config"

    child = script._public_child_environment(
        docker_host="unix:///private/tmp/docker.sock",
        client_home=client_home,
        docker_config=docker_config,
    )

    assert child == {
        "DOCKER_CONFIG": str(docker_config),
        "DOCKER_HOST": "unix:///private/tmp/docker.sock",
        "HOME": str(client_home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "SOURCE_DATE_EPOCH": "0",
    }
    assert not {
        "AWS_ACCESS_KEY_ID",
        "XDG_CONFIG_HOME",
        "HTTP_PROXY",
        "CUSTOM_AUTH_FILE",
        "ACCESS_TOKEN",
    }.intersection(child)
    assert child["HOME"] != inherited["HOME"]
    assert child["DOCKER_CONFIG"] != inherited["DOCKER_CONFIG"]


def test_image_outputs_detect_any_git_worktree(tmp_path: Path) -> None:
    script, _ = _script()
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    worktree = tmp_path / "linked"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")

    assert script._inside_git_worktree(ordinary) is False
    assert script._inside_git_worktree(worktree / "output.json") is True


def test_image_task_loader_requires_exact_authoritative_splits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.test_improvement_loop import VISIBLE_ROOT, _all_task_authorities, _manifest

    script, _ = _script()
    heldout = tuple(
        task for task in _all_task_authorities(_manifest()) if task.split_id == "held-out"
    )
    paths = []
    for task in heldout:
        path = tmp_path / f"{task.case.case_id}.json"
        path.write_bytes(task.canonical_bytes())
        paths.append(path)

    tasks = script._load_tasks(VISIBLE_ROOT, tuple(paths))

    assert len(tasks) == 9
    assert tuple(sorted(item.split_id for item in tasks)) == (
        "development",
        "development",
        "development",
        "held-out",
        "held-out",
        "held-out",
        "regression",
        "regression",
        "regression",
    )

    _, module = _script()
    original = cast(Callable[[Path], ControlledTaskAuthority], module.load_visible_task)

    def drifted(path: Path) -> ControlledTaskAuthority:
        task = original(path)
        if task.case.case_id == "dev-integer-ledger":
            return task.model_copy(update={"split_id": "regression"})
        return task

    monkeypatch.setattr(module, "load_visible_task", drifted)
    with pytest.raises(ContractError, match="3/3/3"):
        cast(_Script, module)._load_tasks(VISIBLE_ROOT, tuple(paths))


def test_heldout_task_root_is_a_closed_three_file_tree(tmp_path: Path) -> None:
    from tests.unit.test_improvement_loop import _all_task_authorities, _manifest

    script, _ = _script()
    root = tmp_path / "heldout"
    root.mkdir()
    heldout = tuple(
        task for task in _all_task_authorities(_manifest()) if task.split_id == "held-out"
    )
    for index, task in enumerate(heldout):
        (root / f"task-{index}.json").write_bytes(task.canonical_bytes())

    assert len(script._heldout_task_paths(root)) == 3
    assert len(_plan_script()._heldout_task_paths(root)) == 3
    (root / "extra").mkdir()
    with pytest.raises(ContractError, match="exactly three"):
        script._heldout_task_paths(root)
    with pytest.raises(ContractError, match="exactly three"):
        _plan_script()._heldout_task_paths(root)
