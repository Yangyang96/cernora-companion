from __future__ import annotations

from pathlib import Path

import pytest

import cernora_reference_workflow.execution as execution_module
from cernora_reference_workflow.common import ContractError, closed_regular_tree
from cernora_reference_workflow.execution import (
    initialize_execution,
    publish_checkpoint,
    publish_execution_manifest,
    publish_execution_pack,
    publish_trial_manifest,
    publish_trial_result,
    rebuild_execution_pack,
    reload_execution,
    start_attempt,
    verify_execution_pack,
)
from cernora_reference_workflow.export import publish_completed_export
from cernora_reference_workflow.offline import evaluate_frozen_export
from cernora_reference_workflow.report_builder import build_run_report
from tests.unit.test_execution import (
    publish_terminal,
    publish_unavailable_result,
    small_plan,
)
from tests.unit.test_export import materialize_staging


def completed_unavailable_execution(root: Path, *, publish_manifest: bool = True) -> None:
    state = initialize_execution(root, small_plan(), nonce="3" * 64)
    for slot in state.trial_slots.slots:
        start_attempt(root, slot.trial_id)
        publish_terminal(
            root,
            trial_id=slot.trial_id,
            experiment_id=slot.slot.experiment_id,
            ordinal=1,
        )
        publish_unavailable_result(root, slot.trial_id)
        publish_trial_manifest(root, slot.trial_id)
    publish_checkpoint(root, status="completed")
    if publish_manifest:
        publish_execution_manifest(root)


def test_evaluated_trial_result_binds_report_evaluation_and_portable_spec(
    tmp_path: Path,
) -> None:
    root = tmp_path / "execution"
    state = initialize_execution(root, small_plan(repetitions=1), nonce="4" * 64)
    slot = state.trial_slots.slots[0]
    spec = state.run_plan.experiment_specs[0]
    start_attempt(root, slot.trial_id)
    attempt = root / "attempts" / slot.trial_id / "0001"
    export_staging = attempt.parent / ".export-staging"
    manifest_fields = materialize_staging(export_staging)
    manifest_fields["experiment_id"] = spec.experiment_id
    publish_completed_export(export_staging, attempt, manifest_fields=manifest_fields)
    offline_root = tmp_path / "offline"
    evaluation = evaluate_frozen_export(
        spec=spec,
        export_root=attempt,
        output_root=offline_root,
    )
    report = build_run_report(
        spec=spec,
        export_root=attempt,
        evaluation=evaluation,
        portable_spec_path=f"specs/{spec.experiment_id}.json",
        portable_export_path=f"attempts/{slot.trial_id}/0001",
        portable_bundle_path=f"results/{slot.trial_id}/offline-evaluation/adapted/bundle.json",
        portable_evaluation_path=f"results/{slot.trial_id}/offline-evaluation/evaluated",
    )
    with pytest.raises(ContractError, match="available strict evaluation"):
        publish_trial_result(root, slot.trial_id, report=report)
    result = publish_trial_result(
        root,
        slot.trial_id,
        report=report,
        offline_evaluation_root=offline_root,
    )
    publish_trial_manifest(root, slot.trial_id)

    assert result.evaluation.status == "available"
    assert reload_execution(root).trial_results == (result,)
    tampered = root / "results" / slot.trial_id / "offline-evaluation/evaluated/case-decision.json"
    tampered.write_bytes(b"{}")
    with pytest.raises((ContractError, ValueError)):
        reload_execution(root)


def test_diagnostic_pack_verification_and_offline_rebuild_are_byte_identical(
    tmp_path: Path,
) -> None:
    execution = tmp_path / "execution"
    completed_unavailable_execution(execution)
    diagnostic = (execution / "diagnostic/diagnostic.md").read_text(encoding="utf-8")
    forbidden = ("quality rate", "winner", "comparison", "aggregate conclusion")
    assert all(term not in diagnostic.lower() for term in forbidden)

    pack = tmp_path / "pack"
    manifest = publish_execution_pack(execution, pack)
    assert verify_execution_pack(pack) == manifest
    rebuilt = tmp_path / "rebuilt"
    rebuild_execution_pack(pack, rebuilt)
    original_files = {
        name: path.read_bytes() for name, path in closed_regular_tree(execution).items()
    }
    rebuilt_files = {name: path.read_bytes() for name, path in closed_regular_tree(rebuilt).items()}
    assert rebuilt_files == original_files
    with pytest.raises(ContractError, match="destination"):
        rebuild_execution_pack(pack, rebuilt)


def test_manifest_publication_recovers_from_preexisting_atomic_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution = tmp_path / "execution"
    completed_unavailable_execution(execution, publish_manifest=False)
    original = execution_module._publish_file

    def fail_manifest(path: Path, payload: bytes) -> None:
        raise RuntimeError("simulated crash after diagnostic publication")

    monkeypatch.setattr(execution_module, "_publish_file", fail_manifest)
    with pytest.raises(RuntimeError, match="simulated crash"):
        publish_execution_manifest(execution)
    assert reload_execution(execution).diagnostic is not None
    assert not (execution / "execution-manifest.json").exists()

    monkeypatch.setattr(execution_module, "_publish_file", original)
    manifest = publish_execution_manifest(execution)
    assert reload_execution(execution).manifest == manifest


@pytest.mark.parametrize("mutation", ("tamper", "unknown", "missing"))
def test_execution_pack_fails_closed_on_tree_mutation(tmp_path: Path, mutation: str) -> None:
    execution = tmp_path / "execution"
    completed_unavailable_execution(execution)
    pack = tmp_path / "pack"
    publish_execution_pack(execution, pack)
    if mutation == "tamper":
        (pack / "execution/diagnostic/diagnostic.md").write_text("tampered", encoding="utf-8")
    elif mutation == "unknown":
        (pack / "execution/unknown.json").write_text("{}", encoding="utf-8")
    else:
        (pack / "execution/diagnostic/diagnostic.md").unlink()
    with pytest.raises((ContractError, ValueError)):
        verify_execution_pack(pack)
