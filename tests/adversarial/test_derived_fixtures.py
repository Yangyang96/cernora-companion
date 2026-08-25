from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.derived_fixtures import (
    MutationName,
    derive_invalid_fixture,
    plant_fake_secret_and_require_rejection,
    verify_derived_fixture,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.export import (
    ExportError,
    publish_completed_export,
    verify_completed_export,
)
from cernora_reference_workflow.offline import evaluate_frozen_export
from cernora_reference_workflow.secrets import SecretScanError
from cernora_reference_workflow.spec_builder import build_tiny_calculator_spec

from ..unit.test_export import materialize_staging

ROOT = Path(__file__).resolve().parents[2]


def _source(tmp_path: Path) -> tuple[ExperimentSpec, Path]:
    spec = build_tiny_calculator_spec(ROOT)
    staging = tmp_path / "source-staging"
    export = tmp_path / "source-export"
    fields = materialize_staging(staging)
    fields["experiment_id"] = spec.experiment_id
    publish_completed_export(staging, export, manifest_fields=fields)
    return spec, export


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-required-artifact",
        "content-digest-mismatch",
        "profile-authority-mismatch",
        "test-runner-authority-mismatch",
    ),
)
def test_derived_fixture_is_labeled_immutable_and_fails_closed(
    tmp_path: Path,
    mutation: MutationName,
) -> None:
    spec, source = _source(tmp_path)
    fixture = tmp_path / f"fixture-{mutation}"
    manifest = derive_invalid_fixture(
        source_export=source,
        source_spec=spec,
        mutation=mutation,
        destination=fixture,
    )
    assert verify_derived_fixture(fixture) == manifest
    mutated_export = fixture / "export"
    if mutation == "profile-authority-mismatch":
        mutated_spec = ExperimentSpec.from_file(fixture / "experiment-spec.json")
        with pytest.raises(
            ContractError,
            match="ExperimentSpec does not bind the loaded Profile authority",
        ):
            evaluate_frozen_export(
                spec=mutated_spec,
                export_root=mutated_export,
                output_root=tmp_path / "offline",
            )
    else:
        with pytest.raises((ExportError, ValueError)):
            verify_completed_export(mutated_export)


def test_planted_fake_secret_rejects_publication(tmp_path: Path) -> None:
    _, source = _source(tmp_path)
    with pytest.raises(SecretScanError):
        plant_fake_secret_and_require_rejection(
            source_export=source,
            staging_parent=tmp_path,
        )
