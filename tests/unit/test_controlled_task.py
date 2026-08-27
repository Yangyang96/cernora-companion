from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
    reconstructed_revealed_case,
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


@pytest.mark.parametrize(
    "modules",
    (
        (
            "cernora_reference_workflow.controlled_task",
            "cernora_reference_workflow.heldout_seal",
        ),
        (
            "cernora_reference_workflow.heldout_seal",
            "cernora_reference_workflow.controlled_task",
        ),
    ),
)
def test_controlled_task_and_heldout_seal_import_in_either_order(
    modules: tuple[str, str],
) -> None:
    program = "import sys; sys.path.insert(0, 'src'); " + "; ".join(
        f"__import__({module!r})" for module in modules
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _generic_case() -> HeldoutArchiveCase:
    return HeldoutArchiveCase(
        case_id="case-00000000000000000000000000000001",
        task={
            "schema_version": "cernora.reference.heldout-task/v1",
            "language": "python",
            "instruction": "Repair the module without changing its frozen verifier.",
            "allowed_paths": ["src/main.py"],
            "protected_paths": ["tests/verify.py"],
            "case_version": "1",
        },
        workspace={
            "schema_version": "cernora.reference.heldout-workspace/v1",
            "files": [
                {"path": "tests/verify.py", "content_utf8": "assert VALUE == 2\n"},
                {"path": "src/main.py", "content_utf8": "VALUE = 1\n"},
            ],
        },
        evaluation={
            "schema_version": "cernora.reference.heldout-evaluation/v1",
            "command": ["python", "tests/verify.py"],
            "working_directory": ".",
            "timeout_seconds": 60,
            "network": "disabled",
            "expected_exit_code": 0,
            "success_metric": "verifier_exit_zero",
            "failure_codes": ["wrong_value_v1"],
        },
    )


def test_revealed_generic_case_conversion_is_exactly_reversible() -> None:
    case = _generic_case()
    task = task_from_revealed_case(case)

    assert task.split_id == "held-out"
    assert task.failure_code == "wrong_value_v1"
    assert task.allowed_paths == ("src/main.py",)
    assert task.protected_paths == ("tests/verify.py",)
    assert reconstructed_revealed_case(task) == case


@pytest.mark.parametrize(
    "mutation",
    ("missing", "extra", "ambiguous", "wrong-type", "multiple-failures"),
)
def test_revealed_case_decoder_rejects_invalid_generic_projection(mutation: str) -> None:
    payload = _generic_case().model_dump(mode="json")
    task = payload["task"]
    workspace = payload["workspace"]
    evaluation = payload["evaluation"]
    assert isinstance(task, dict)
    assert isinstance(workspace, dict)
    assert isinstance(evaluation, dict)
    if mutation == "missing":
        task.pop("language")
    elif mutation == "extra":
        task["undeclared"] = "hidden"
    elif mutation == "ambiguous":
        files = workspace["files"]
        assert isinstance(files, list)
        files.append(dict(files[0]))
    elif mutation == "wrong-type":
        evaluation["timeout_seconds"] = True
    else:
        evaluation["failure_codes"] = ["wrong_value_v1", "second_failure_v1"]
    case = HeldoutArchiveCase.model_validate(payload)

    with pytest.raises((ValidationError, ValueError), match="revealed Case|controlled task"):
        task_from_revealed_case(case)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        ("task", "schema_version", "cernora.reference.heldout-task/v2"),
        ("task", "language", "ruby"),
        ("task", "case_version", "2"),
        ("workspace", "schema_version", "cernora.reference.heldout-workspace/v2"),
        ("evaluation", "schema_version", "cernora.reference.heldout-evaluation/v2"),
        ("evaluation", "working_directory", "/tmp"),
        ("evaluation", "timeout_seconds", 61),
        ("evaluation", "timeout_seconds", True),
        ("evaluation", "network", "enabled"),
        ("evaluation", "expected_exit_code", 1),
        ("evaluation", "expected_exit_code", False),
        ("evaluation", "success_metric", "tests_passed"),
    ),
)
def test_revealed_case_decoder_rejects_frozen_semantic_drift(
    section: str,
    field: str,
    value: object,
) -> None:
    payload = _generic_case().model_dump(mode="json")
    projection = payload[section]
    assert isinstance(projection, dict)
    projection[field] = value

    with pytest.raises((ValidationError, ValueError), match="revealed Case|controlled task"):
        task_from_revealed_case(HeldoutArchiveCase.model_validate(payload))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("task_schema_version", "cernora.reference.heldout-task/v2"),
        ("language", "ruby"),
        ("case_version", "2"),
        ("workspace_schema_version", "cernora.reference.heldout-workspace/v2"),
        ("evaluation_schema_version", "cernora.reference.heldout-evaluation/v2"),
        ("working_directory", "/tmp"),
        ("timeout_seconds", 61),
        ("timeout_seconds", True),
        ("network", "enabled"),
        ("expected_exit_code", 1),
        ("expected_exit_code", False),
        ("success_metric", "tests_passed"),
    ),
)
def test_reidentified_task_rejects_frozen_binding_drift(field: str, value: object) -> None:
    task = task_from_revealed_case(_generic_case())
    payload = task.model_dump(mode="json", exclude={"authority_id"})
    case = payload["case"]
    assert isinstance(case, dict)
    inputs = case["input"]
    assert isinstance(inputs, dict)
    parameters = inputs["parameters"]
    assert isinstance(parameters, dict)
    binding = parameters["heldout_archive_v1"]
    assert isinstance(binding, dict)
    binding[field] = value

    with pytest.raises(ValidationError, match="held-out|literal|integer"):
        ControlledTaskAuthority.model_validate({**payload, "authority_id": "0" * 64})
