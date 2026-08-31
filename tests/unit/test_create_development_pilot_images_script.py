from __future__ import annotations

import contextlib
import importlib.util
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.development_agent_pilot import (
    PILOT_CASE_IDS,
    DevelopmentPilotImageSet,
)

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "examples" / "priority4-development-pilot"


def _script() -> ModuleType:
    path = ROOT / "scripts" / "create_development_pilot_images.py"
    specification = importlib.util.spec_from_file_location("create_development_images", path)
    if specification is None or specification.loader is None:
        raise AssertionError("could not load development image script")
    module = importlib.util.module_from_spec(specification)
    sys.path.insert(0, str(path.parent))
    try:
        specification.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def test_development_image_builder_uses_only_fresh_visible_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _script()
    accepted_base = cast(str, module.ACCEPTED_M4_RUNTIME_BASE)
    observed: dict[str, object] = {}

    @contextlib.contextmanager
    def environment(_work_root: Path) -> Iterator[dict[str, str]]:
        yield {"DOCKER_HOST": "unix:///private/tmp/fake.sock"}

    def build(
        tasks: tuple[ControlledTaskAuthority, ...],
        *,
        build_base_image: str,
        work_root: Path,
        environment: Mapping[str, str],
        image_prefix: str,
    ) -> dict[str, str]:
        observed.update(
            tasks=tasks,
            build_base_image=build_base_image,
            work_root=work_root,
            environment=environment,
            image_prefix=image_prefix,
        )
        return {
            case_id: f"{image_prefix}{case_id}@sha256:{index:064x}"
            for index, case_id in enumerate(PILOT_CASE_IDS, start=1)
        }

    monkeypatch.setattr(module, "_isolated_docker_environment", environment)
    monkeypatch.setattr(module, "_build_all_images", build)
    work_root = tmp_path / "work"
    work_root.mkdir()
    output = tmp_path / "images.json"

    module.create_development_pilot_images(
        corpus_root=CORPUS,
        build_base_image=accepted_base,
        work_root=work_root,
        output=output,
    )

    authority = DevelopmentPilotImageSet.from_file(output)
    tasks = cast(tuple[ControlledTaskAuthority, ...], observed["tasks"])
    assert tuple(item.case.case_id for item in tasks) == PILOT_CASE_IDS
    assert {item.split_id for item in tasks} == {"development", "regression"}
    assert observed["image_prefix"] == "cernora-reference/p4-pilot-"
    assert authority.image_set_id
