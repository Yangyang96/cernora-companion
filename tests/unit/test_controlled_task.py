from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
    task_from_revealed_case,
)
from cernora_reference_workflow.heldout_seal import HeldoutArchiveCase


def test_visible_task_is_strict_content_identified_authority() -> None:
    root = Path("examples/m4-visible/dev-interval-merge")
    task = load_visible_task(root)

    assert task.case.case_id == "dev-interval-merge"
    assert task.split_id == "development"
    assert task.allowed_paths == ("src/intervals.py",)
    assert task.protected_paths == ("tests/verify.py",)
    assert ControlledTaskAuthority.from_bytes(task.canonical_bytes()) == task


def test_visible_task_identity_changes_with_workspace_bytes(tmp_path: Path) -> None:
    source = Path("examples/m4-visible/dev-interval-merge")
    copy = tmp_path / "case"
    copy.mkdir()
    for item in source.iterdir():
        (copy / item.name).write_bytes(item.read_bytes())
    first = load_visible_task(copy)
    (copy / "baseline.py").write_text("def merge(value): return value\n", encoding="utf-8")

    assert load_visible_task(copy).authority_id != first.authority_id


def test_revealed_case_decoder_requires_full_generic_authority_shape() -> None:
    case = HeldoutArchiveCase(
        case_id="case-00000000000000000000000000000001",
        task={"instruction": "opaque"},
        workspace={"files": [{"path": "src/main.py", "content": "pass"}]},
        evaluation={"command": "pytest -q", "expected": "pass"},
    )

    with pytest.raises((ValidationError, ValueError), match="controlled task authority"):
        task_from_revealed_case(case)
