"""Strict offline export binding, adaptation, import, evaluation, and reload."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from cernora import (
    CompletedExport,
    evaluate_imported_case,
    import_evidence_bundle_v2,
    read_imported_evaluation,
)

from cernora_reference_workflow.adapter import ReferenceCodingAdapter
from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    load_json_file,
    sha256_bytes,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.export import CompletedExportManifest, verify_completed_export
from cernora_reference_workflow.profile import create_profile
from cernora_reference_workflow.runtime_observation import ContainerImageObservation
from cernora_reference_workflow.secrets import require_secret_free


class StrictReloadReceipt(Protocol):
    case_outcome: Literal["pass", "fail", "inconclusive"]
    evaluation_id: str
    evidence_id: str
    score_id: str
    decision_id: str
    evaluation_input_sha256: str


@dataclass(frozen=True)
class OfflineEvaluation:
    bundle_path: Path
    import_root: Path
    evaluation_root: Path
    receipt: StrictReloadReceipt


def verify_workflow_binding(
    spec: ExperimentSpec,
    manifest: CompletedExportManifest,
) -> None:
    profile = create_profile()
    authority = profile.authority
    authority_sha256 = sha256_bytes(
        canonical_json_bytes(authority.model_dump(mode="json", exclude_none=False))
    )
    if manifest.experiment_id != spec.experiment_id:
        raise ContractError("completed export does not bind the supplied ExperimentSpec")
    if (
        authority.profile_id != spec.profile.profile_id
        or authority.profile_version != spec.profile.profile_version
        or authority_sha256 != spec.profile.authority_sha256
    ):
        raise ContractError("ExperimentSpec does not bind the loaded Profile authority")
    if (
        manifest.test_authority_sha256 != spec.test_runner.authority_sha256
        or manifest.test_plan_sha256 != spec.test_runner.test_plan_sha256
    ):
        raise ContractError("completed export does not bind the ExperimentSpec Test Runner")


def verify_container_image_binding(spec: ExperimentSpec, export_root: Path) -> None:
    receipt_path = export_root / "runtime/container-image.json"
    if not receipt_path.is_file():
        return
    payload = load_json_file(receipt_path)
    if not isinstance(payload, dict):
        raise ContractError("container image observation must be a JSON object")
    receipt = ContainerImageObservation.model_validate(payload)
    if receipt.task_image_reference != spec.container.image:
        raise ContractError("completed export does not bind the ExperimentSpec task image")


def evaluate_frozen_export(
    *,
    spec: ExperimentSpec,
    export_root: Path,
    output_root: Path,
) -> OfflineEvaluation:
    """Evaluate without Runtime, shell, Docker, Git, network, or test execution."""

    if output_root.exists() or output_root.is_symlink():
        raise ContractError("offline evaluation output must not already exist")
    if not output_root.parent.is_dir():
        raise ContractError("offline evaluation parent must already exist")
    manifest = verify_completed_export(export_root)
    verify_workflow_binding(spec, manifest)
    verify_container_image_binding(spec, export_root)
    output_root.mkdir()

    profile = create_profile()
    bundle_root = output_root / "adapted"
    adapted = ReferenceCodingAdapter(profile).adapt(CompletedExport(root=export_root), bundle_root)
    require_secret_free(bundle_root)
    import_root = output_root / "imported"
    import_evidence_bundle_v2(
        profile=profile,
        bundle_path=adapted.bundle_path,
        output=import_root,
    )
    require_secret_free(import_root)
    evaluation_root = output_root / "evaluated"
    receipt = evaluate_imported_case(profile, import_root, evaluation_root)
    require_secret_free(evaluation_root)
    reloaded = read_imported_evaluation(evaluation_root, profile)
    if receipt != reloaded:
        raise ContractError("strict reload does not match the published evaluation receipt")
    return OfflineEvaluation(
        bundle_path=adapted.bundle_path,
        import_root=import_root,
        evaluation_root=evaluation_root,
        receipt=reloaded,
    )


__all__ = [
    "OfflineEvaluation",
    "evaluate_frozen_export",
    "verify_container_image_binding",
    "verify_workflow_binding",
]
