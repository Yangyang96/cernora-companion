from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.controlled_run_plan import materialize_controlled_run_plan
from cernora_reference_workflow.controlled_task import load_visible_task
from tests.unit.test_controlled_run_plan import valid_m4_payload, valid_payload


class _Script(Protocol):
    def validate_task_suite(self, plan: object, tasks: tuple[object, ...]) -> None: ...


def _script() -> tuple[_Script, ModuleType]:
    path = Path(__file__).resolve().parents[2] / "scripts/run_m4_controlled.py"
    specification = importlib.util.spec_from_file_location("run_m4_controlled", path)
    if specification is None or specification.loader is None:
        raise AssertionError("could not load M4 controlled script")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return cast(_Script, module), module


def test_script_rejects_m3_plan_and_nonexhaustive_task_authorities() -> None:
    script, _ = _script()
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    m3 = materialize_controlled_run_plan(valid_payload())
    m4 = materialize_controlled_run_plan(valid_m4_payload())

    with pytest.raises(ContractError, match="0.4.0"):
        script.validate_task_suite(m3, (task,))
    with pytest.raises(ContractError, match="exactly exhaust"):
        script.validate_task_suite(m4, (task,))
