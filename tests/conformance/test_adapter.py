from __future__ import annotations

from pathlib import Path

from cernora import (
    CompletedExport,
    check_adapter_conformance,
    evaluate_imported_case,
    import_evidence_bundle_v2,
    read_imported_evaluation,
)

from cernora_reference_workflow.adapter import ReferenceCodingAdapter
from cernora_reference_workflow.common import closed_regular_tree
from cernora_reference_workflow.export import publish_completed_export
from cernora_reference_workflow.profile import create_profile

from ..unit.test_export import materialize_staging


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {relative: path.read_bytes() for relative, path in closed_regular_tree(root).items()}


def test_public_adapter_import_evaluation_reload_and_three_run_identity(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    export = tmp_path / "completed-export"
    fields = materialize_staging(staging)
    publish_completed_export(staging, export, manifest_fields=fields)

    profile = create_profile()
    adapter = ReferenceCodingAdapter(profile)
    adapted_trees: list[dict[str, bytes]] = []
    import_trees: list[dict[str, bytes]] = []
    evaluation_trees: list[dict[str, bytes]] = []

    for index in range(3):
        adapted = tmp_path / f"adapted-{index}"
        conformance = check_adapter_conformance(adapter, CompletedExport(root=export), adapted)
        imported = tmp_path / f"imported-{index}"
        import_evidence_bundle_v2(
            profile=profile,
            bundle_path=conformance.bundle_path,
            output=imported,
        )
        evaluated = tmp_path / f"evaluated-{index}"
        receipt = evaluate_imported_case(profile, imported, evaluated)
        reloaded = read_imported_evaluation(evaluated, profile)
        assert receipt == reloaded
        assert receipt.case_outcome == "pass"
        adapted_trees.append(_tree_bytes(adapted))
        import_trees.append(_tree_bytes(imported))
        evaluation_trees.append(_tree_bytes(evaluated))

    assert adapted_trees[0] == adapted_trees[1] == adapted_trees[2]
    assert import_trees[0] == import_trees[1] == import_trees[2]
    assert evaluation_trees[0] == evaluation_trees[1] == evaluation_trees[2]
