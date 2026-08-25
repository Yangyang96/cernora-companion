from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.experiment_spec import materialize_experiment_spec
from cernora_reference_workflow.export import publish_completed_export
from cernora_reference_workflow.offline import (
    evaluate_frozen_export,
    verify_container_image_binding,
)
from cernora_reference_workflow.profile import create_profile
from cernora_reference_workflow.runtime_observation import ContainerImageObservation
from cernora_reference_workflow.spec_builder import build_tiny_calculator_spec

from ..unit.test_export import materialize_staging

ROOT = Path(__file__).resolve().parents[2]


def test_offline_pipeline_rejects_experiment_profile_mismatch(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    export = tmp_path / "completed"
    fields = materialize_staging(staging)
    source_spec = build_tiny_calculator_spec(ROOT)
    fields["experiment_id"] = source_spec.experiment_id
    publish_completed_export(staging, export, manifest_fields=fields)

    payload = source_spec.model_dump(mode="json", exclude={"experiment_id"})
    profile_payload = payload["profile"]
    assert isinstance(profile_payload, dict)
    profile_payload["authority_sha256"] = "f" * 64
    spec = materialize_experiment_spec(payload)
    with pytest.raises(ContractError, match="ExperimentSpec"):
        evaluate_frozen_export(spec=spec, export_root=export, output_root=tmp_path / "result")


def test_loaded_profile_is_the_explicit_companion_authority() -> None:
    profile = create_profile()
    assert profile.authority.profile_id == "cernora-reference-coding-v1"


def test_offline_pipeline_rejects_task_image_reference_mismatch(tmp_path: Path) -> None:
    spec = build_tiny_calculator_spec(ROOT)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    receipt = ContainerImageObservation(
        schema_version="cernora.reference.container-image-observation/v1",
        task_image_reference=f"cernora-reference/other@sha256:{'a' * 64}",
        observed_image_id=f"sha256:{'a' * 64}",
        matched=True,
    )
    (runtime / "container-image.json").write_bytes(
        canonical_json_bytes(receipt.model_dump(mode="json"))
    )

    with pytest.raises(ContractError, match="does not bind the ExperimentSpec task image"):
        verify_container_image_binding(spec, tmp_path)
