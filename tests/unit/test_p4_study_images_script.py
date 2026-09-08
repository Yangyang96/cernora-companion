from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    task_from_revealed_case,
)

REPOSITORY = Path(__file__).resolve().parents[2]
PILOT_IMAGES = (
    REPOSITORY / "preparations" / "next-priority4-development-pilot-pi-r9" / "images.json"
)


class _ImagesScript(Protocol):
    def load_pilot_images(self, path: Path) -> tuple[dict[str, str], str]: ...

    def load_revealed_tasks(self, root: Path) -> tuple[ControlledTaskAuthority, ...]: ...

    def merge_study_images(
        self, pilot_images: dict[str, str], built_images: dict[str, str], base: str
    ) -> dict[str, object]: ...


def _load_script() -> _ImagesScript:
    location = REPOSITORY / "scripts" / "create_p4_study_images.py"
    scripts_directory = str(location.parent)
    sys.path.insert(0, scripts_directory)
    try:
        spec = importlib.util.spec_from_file_location("create_p4_study_images", location)
        assert spec is not None and spec.loader is not None
        module: ModuleType = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_directory)
    return cast(_ImagesScript, module)


def test_load_pilot_images_reads_the_frozen_r9_set() -> None:
    script = _load_script()

    images, build_base = script.load_pilot_images(PILOT_IMAGES)

    assert len(images) == 9
    assert "p4-dev-csv-quoted" in images
    assert build_base.startswith("cernora-reference/pi-runtime@sha256:")


def test_load_pilot_images_rejects_schema_drift(tmp_path: Path) -> None:
    script = _load_script()
    payload = tmp_path / "images.json"
    payload.write_text('{"schema_version": "cernora.reference.drift/v1"}')

    with pytest.raises(Exception, match="frozen v2 schema"):
        script.load_pilot_images(payload)


def _fake_reveal_layout(revealed_root: Path) -> None:
    from cernora_reference_workflow.heldout_seal import HeldoutArchiveCase
    from tests.unit.test_p4_heldout_reveal_script import _fake_cases

    for case in _fake_cases():
        authority = task_from_revealed_case(HeldoutArchiveCase.model_validate(case))
        case_id = authority.case.case_id
        (revealed_root / f"{case_id}.json").write_bytes(authority.canonical_bytes())
    (revealed_root / "reveal-receipt.json").write_bytes(b"receipt-placeholder")


def test_load_revealed_tasks_requires_the_full_reveal_layout(tmp_path: Path) -> None:
    script = _load_script()
    revealed_root = tmp_path / "revealed"
    revealed_root.mkdir()
    _fake_reveal_layout(revealed_root)

    tasks = script.load_revealed_tasks(revealed_root)

    assert len(tasks) == 3
    assert all(task.split_id == "held-out" for task in tasks)

    (revealed_root / "reveal-receipt.json").unlink()
    with pytest.raises(Exception, match="complete reveal output"):
        script.load_revealed_tasks(revealed_root)


def test_merge_study_images_covers_exactly_twelve_sorted_cases() -> None:
    script = _load_script()
    pilot = {f"p4-dev-case-{index}": f"ref-{index}@sha256:{'a' * 64}" for index in range(9)}
    built = {
        f"case-{index:032x}": f"cernora-reference/p4-study-case-{index:032x}@sha256:{'b' * 64}"
        for index in range(1, 4)
    }

    payload = script.merge_study_images(pilot, built, "base@sha256:" + "c" * 64)

    entries = payload["images"]
    assert isinstance(entries, list)
    assert [item["case_id"] for item in entries] == sorted({**pilot, **built}, key=str)
    assert payload["image_set_id"] == payload["image_set_id"]
    canonical = canonical_json_bytes(payload)
    assert b"p4-dev-case-0" in canonical and b"case-" in canonical

    with pytest.raises(Exception, match="overlap"):
        overlapping = dict(pilot)
        overlapping[next(iter(built))] = pilot["p4-dev-case-0"]
        script.merge_study_images(overlapping, built, "base")
