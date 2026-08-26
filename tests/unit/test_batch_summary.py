from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from cernora import build_batch_summary

from cernora_reference_workflow.batch_summary import (
    _selected_resources,
    normalize_execution_pack,
    summarize_execution_pack,
)
from cernora_reference_workflow.cli import main
from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.execution import (
    initialize_execution,
    publish_checkpoint,
    publish_execution_manifest,
    publish_execution_pack,
    publish_trial_manifest,
    reload_execution,
    start_attempt,
)
from cernora_reference_workflow.report import AvailableMetric, RunReport
from tests.unit.test_execution import (
    publish_terminal,
    publish_unavailable_result,
    small_plan,
)
from tests.unit.test_execution_pack import completed_unavailable_execution


def _variant_unavailable_pack(root: Path, *, source_suffix: str) -> None:
    execution = root.with_name(f"{root.name}-execution")
    state = initialize_execution(execution, small_plan(), nonce="3" * 64)
    for slot in state.trial_slots.slots:
        start_attempt(execution, slot.trial_id)
        publish_terminal(
            execution,
            trial_id=slot.trial_id,
            experiment_id=slot.slot.experiment_id,
            ordinal=1,
            source_trial_id=f"source-{source_suffix}-{slot.slot.slot_index}",
        )
        publish_unavailable_result(execution, slot.trial_id)
        publish_trial_manifest(execution, slot.trial_id)
    publish_checkpoint(execution, status="completed")
    publish_execution_manifest(execution)
    publish_execution_pack(execution, root)


def test_unavailable_execution_pack_preserves_zero_valid_group(tmp_path: Path) -> None:
    execution = tmp_path / "execution"
    completed_unavailable_execution(execution)
    pack = tmp_path / "execution.pack"
    publish_execution_pack(execution, pack)

    batch_input = normalize_execution_pack(pack)
    summary = build_batch_summary(batch_input)

    assert batch_input.planned_trial_count == 2
    assert batch_input.attempt_count == 2
    assert summary.overall.outcomes.model_dump() == {
        "passed": 0,
        "behavioral_failed": 0,
        "evaluation_invalid": 0,
        "infrastructure_unavailable": 2,
    }
    assert summary.by_cell[0].behavioral_success_rate.model_dump() == {
        "numerator": 0,
        "denominator": 0,
        "value": None,
    }
    assert all(
        trial.attempts[0].lifecycle is not None
        and trial.attempts[0].lifecycle.category == "runtime_pre_terminal_failure"
        and trial.attempts[0].resources.duration_milliseconds is None
        for trial in batch_input.trials
    )


def test_selected_token_metrics_require_one_authoritative_receipt(tmp_path: Path) -> None:
    execution = tmp_path / "execution"
    completed_unavailable_execution(execution)
    trial_id = next((execution / "results").iterdir()).name
    report = RunReport.from_file(execution / "results" / trial_id / "run-report.json")
    diagnostics = report.diagnostics.model_copy(
        update={
            "input_tokens": AvailableMetric(
                status="available",
                value=10,
                unit="tokens",
                source_receipt_sha256="a" * 64,
            ),
            "output_tokens": AvailableMetric(
                status="available",
                value=20,
                unit="tokens",
                source_receipt_sha256="b" * 64,
            ),
        }
    )

    with pytest.raises(ContractError, match="one authoritative usage receipt"):
        _selected_resources(report.model_copy(update={"diagnostics": diagnostics}))


@pytest.mark.parametrize("replacement", ("replace", "aba"))
@pytest.mark.parametrize("interface", ("api", "cli"))
def test_valid_pack_replacement_fails_without_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    replacement: str,
    interface: str,
) -> None:
    pack = tmp_path / "pack"
    alternative = tmp_path / "alternative.pack"
    original_copy = tmp_path / "original.pack"
    _variant_unavailable_pack(pack, source_suffix="a")
    _variant_unavailable_pack(alternative, source_suffix="b")
    shutil.copytree(pack, original_copy)
    original_reload = reload_execution
    replaced = False

    def replace_after_snapshot_reload(root: Path):  # type: ignore[no-untyped-def]
        nonlocal replaced
        state = original_reload(root)
        if not replaced and root != pack / "execution":
            shutil.rmtree(pack)
            shutil.copytree(alternative, pack)
            if replacement == "aba":
                shutil.rmtree(pack)
                shutil.copytree(original_copy, pack)
            replaced = True
        return state

    monkeypatch.setattr(
        "cernora_reference_workflow.batch_summary.reload_execution",
        replace_after_snapshot_reload,
    )
    output = tmp_path / "summary"
    if interface == "api":
        with pytest.raises(ContractError, match="changed during normalization"):
            summarize_execution_pack(pack, output)
    else:
        assert main(["summarize", str(pack), "--output", str(output)]) == 3
        assert "changed during normalization" in capsys.readouterr().err
    assert replaced
    assert not output.exists()
    assert not tuple(tmp_path.glob(f".{output.name}.staging-*"))
