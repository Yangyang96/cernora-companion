from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest
from cernora import summarize_batch

from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.controlled_task import load_visible_task
from cernora_reference_workflow.heldout_seal import HeldoutManifest
from cernora_reference_workflow.improvement_loop import (
    CandidateFreeze,
    CandidateFreezeVerificationReceipt,
)
from tests.unit.test_controlled_run_plan import valid_m4_payload, valid_payload
from tests.unit.test_improvement_loop import (
    VISIBLE_ROOT,
    _all_task_authorities,
    _comparison,
    _final_plan,
    _freeze_and_final,
    _reveal,
)


class _Verified(Protocol):
    freeze: CandidateFreeze
    plan: ControlledRunPlanV2
    verification_receipt: CandidateFreezeVerificationReceipt


class _Script(Protocol):
    def validate_task_suite(self, plan: object, tasks: tuple[object, ...]) -> None: ...

    def load_verified_execution(self, **kwargs: object) -> _Verified: ...


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


def _verified_paths(
    tmp_path: Path,
) -> tuple[dict[str, object], CandidateFreeze, HeldoutManifest]:
    freeze, pilot, plan, comparison, manifest = _freeze_and_final(tmp_path)
    reveal = _reveal(manifest, freeze)
    pilot_root = tmp_path / "pilot-package"
    summarize_batch(pilot.batch_input, pilot_root)
    files = {
        "run_plan_path": (tmp_path / "run-plan.json", plan.canonical_bytes()),
        "candidate_freeze_path": (
            tmp_path / "candidate-freeze.json",
            freeze.canonical_bytes(),
        ),
        "comparison_plan_path": (
            tmp_path / "comparison-plan.json",
            comparison.canonical_bytes(),
        ),
        "heldout_manifest_path": (
            tmp_path / "heldout-manifest.json",
            manifest.canonical_bytes(),
        ),
        "reveal_receipt_path": (
            tmp_path / "reveal-receipt.json",
            reveal.canonical_bytes(),
        ),
    }
    paths: dict[str, object] = {
        "pilot_package_root": pilot_root,
        "visible_corpus_root": VISIBLE_ROOT,
    }
    for name, (path, payload) in files.items():
        path.write_bytes(payload)
        paths[name] = path
    task_paths = []
    for index, task in enumerate(_all_task_authorities(manifest)):
        path = tmp_path / f"task-{index}.json"
        path.write_bytes(task.canonical_bytes())
        task_paths.append(path)
    paths["task_authority_paths"] = tuple(task_paths)
    return paths, freeze, manifest


def test_production_loader_requires_complete_verified_freeze_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script, module = _script()
    paths, freeze, _ = _verified_paths(tmp_path)
    monkeypatch.setattr(module, "validate_task_suite", lambda plan, tasks: None)

    verified = script.load_verified_execution(**paths)

    assert verified.freeze == freeze
    assert len(verified.plan.expand_trial_slots()) == 54
    assert verified.verification_receipt.run_plan_id == verified.plan.run_plan_id


@pytest.mark.parametrize("mutation", ("reidentified-freeze", "mismatched-comparison"))
def test_production_loader_rejects_reidentified_or_mismatched_authorities(
    tmp_path: Path,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script, module = _script()
    paths, freeze, manifest = _verified_paths(tmp_path)
    monkeypatch.setattr(module, "validate_task_suite", lambda plan, tasks: None)
    if mutation == "reidentified-freeze":
        payload = freeze.model_dump(mode="json")
        payload["candidate_freeze_id"] = "candidate-freeze-reidentified"
        candidate_path = cast(Path, paths["candidate_freeze_path"])
        candidate_path.write_bytes(canonical_json_bytes(payload))
    else:
        other = _final_plan(
            manifest,
            candidate_prompt="Apply a separately identified candidate prompt.",
        )
        comparison_path = cast(Path, paths["comparison_plan_path"])
        comparison_path.write_bytes(_comparison(other, manifest).canonical_bytes())

    with pytest.raises((ContractError, ValueError)):
        script.load_verified_execution(**paths)


def test_production_parser_rejects_omitted_freeze_authorities() -> None:
    _, module = _script()
    required = (
        "--candidate-freeze",
        "--comparison-plan",
        "--pilot-package",
        "--visible-corpus-root",
        "--heldout-manifest",
        "--reveal-receipt",
    )

    common = [
        "--run-plan",
        "run-plan.json",
        "--task-authority",
        "task.json",
        "--store",
        "store",
        "--evaluation-root",
        "evaluation",
        "--batch-output",
        "batch",
        "--repository-root",
        "repository",
        "--auth-file",
        "auth.json",
        "--proxy",
        "HTTP_PROXY=http://proxy",
        "--proxy",
        "HTTPS_PROXY=http://proxy",
        "--proxy",
        "ALL_PROXY=socks5://proxy",
        "--nonce",
        "0" * 64,
    ]
    for omitted in required:
        arguments = list(common)
        for option in required:
            if option != omitted:
                arguments.extend((option, "authority"))
        with pytest.raises(SystemExit):
            module._parser().parse_args(arguments)
