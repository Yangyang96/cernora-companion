"""Closed, immutable packet of deterministic failures derived from one real export."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from cernora_reference_workflow.common import (
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
    sha256_bytes,
    sha256_file,
    validate_relative_path,
)
from cernora_reference_workflow.experiment_spec import (
    Digest,
    ExperimentSpec,
    NonEmpty,
    StrictContract,
)
from cernora_reference_workflow.export import verify_completed_export
from cernora_reference_workflow.offline import verify_workflow_binding
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.secrets import SecretScanError, require_secret_free
from tests.support.derived_fixtures import (
    MutationName,
    derive_invalid_fixture,
    plant_fake_secret_and_require_rejection,
    verify_derived_fixture,
)

MUTATIONS: tuple[MutationName, ...] = (
    "missing-required-artifact",
    "content-digest-mismatch",
    "profile-authority-mismatch",
    "test-runner-authority-mismatch",
)


class DerivedMatrixError(ValueError):
    """A derived failure matrix is malformed or cannot be published safely."""


class DerivedMatrixFixture(StrictContract):
    mutation: MutationName
    path: NonEmpty
    fixture_id: Digest
    manifest_sha256: Digest

    @model_validator(mode="after")
    def validate_path(self) -> DerivedMatrixFixture:
        validate_relative_path(self.path)
        if self.path != f"fixtures/{self.mutation}":
            raise ValueError("derived matrix fixture path does not match its mutation")
        return self


class DerivedMatrixManifest(StrictContract):
    schema_version: Literal["cernora.reference.derived-matrix/v1"]
    matrix_id: Digest
    recipe_version: Literal["1"]
    source_export_sha256: Digest
    source_experiment_spec_sha256: Digest
    fixtures: Annotated[tuple[DerivedMatrixFixture, ...], Field(min_length=4, max_length=4)]
    planted_fake_secret: Literal["rejected-before-publication"]

    @model_validator(mode="after")
    def validate_identity(self) -> DerivedMatrixManifest:
        if tuple(item.mutation for item in self.fixtures) != MUTATIONS:
            raise ValueError("derived matrix must contain the complete ordered mutation set")
        expected = sha256_bytes(
            canonical_json_bytes(self.model_dump(mode="json", exclude={"matrix_id"}))
        )
        if self.matrix_id != expected:
            raise ValueError("derived matrix identity mismatch")
        return self


def generate_derived_matrix(
    *,
    source_export: Path,
    source_spec: ExperimentSpec,
    destination: Path,
) -> DerivedMatrixManifest:
    if destination.exists() or destination.is_symlink():
        raise DerivedMatrixError("derived matrix destination must not already exist")
    if not destination.parent.is_dir():
        raise DerivedMatrixError("derived matrix parent must already exist")
    source_manifest = verify_completed_export(source_export)
    verify_workflow_binding(source_spec, source_manifest)

    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        records: list[dict[str, str]] = []
        for mutation in MUTATIONS:
            relative = f"fixtures/{mutation}"
            fixture = staging / relative
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture_manifest = derive_invalid_fixture(
                source_export=source_export,
                source_spec=source_spec,
                mutation=mutation,
                destination=fixture,
            )
            records.append(
                {
                    "mutation": mutation,
                    "path": relative,
                    "fixture_id": fixture_manifest.fixture_id,
                    "manifest_sha256": sha256_file(fixture / "fixture.json"),
                }
            )
        try:
            plant_fake_secret_and_require_rejection(
                source_export=source_export,
                staging_parent=staging,
            )
        except SecretScanError:
            pass
        else:
            raise DerivedMatrixError("planted fake secret did not fail closed")

        payload: dict[str, object] = {
            "schema_version": "cernora.reference.derived-matrix/v1",
            "recipe_version": "1",
            "source_export_sha256": sha256_file(source_export / "manifest.json"),
            "source_experiment_spec_sha256": sha256_bytes(source_spec.canonical_bytes()),
            "fixtures": records,
            "planted_fake_secret": "rejected-before-publication",
        }
        payload["matrix_id"] = sha256_bytes(canonical_json_bytes(payload))
        matrix_manifest = DerivedMatrixManifest.model_validate(payload)
        (staging / "matrix.json").write_bytes(
            canonical_json_bytes(matrix_manifest.model_dump(mode="json"))
        )
        require_secret_free(staging)
        verify_derived_matrix(staging)
        atomic_publish_directory(staging, destination)
        return matrix_manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def verify_derived_matrix(root: Path) -> DerivedMatrixManifest:
    files = closed_regular_tree(root)
    payload = load_json_file(root / "matrix.json")
    if not isinstance(payload, dict):
        raise DerivedMatrixError("derived matrix manifest must contain one JSON object")
    manifest = DerivedMatrixManifest.model_validate(payload)
    if (root / "matrix.json").read_bytes() != canonical_json_bytes(
        manifest.model_dump(mode="json")
    ):
        raise DerivedMatrixError("derived matrix manifest is not canonical JSON")

    expected_files = {"matrix.json"}
    for record in manifest.fixtures:
        fixture_root = root / record.path
        fixture = verify_derived_fixture(fixture_root)
        if fixture.fixture_id != record.fixture_id:
            raise DerivedMatrixError("derived fixture identity does not match matrix record")
        if sha256_file(fixture_root / "fixture.json") != record.manifest_sha256:
            raise DerivedMatrixError("derived fixture manifest digest does not match matrix record")
        if (
            fixture.source_export_sha256 != manifest.source_export_sha256
            or fixture.source_experiment_spec_sha256 != manifest.source_experiment_spec_sha256
        ):
            raise DerivedMatrixError("derived fixture source does not match matrix source")
        expected_files.update(
            f"{record.path}/{relative}" for relative in closed_regular_tree(fixture_root)
        )
    if set(files) != expected_files:
        raise DerivedMatrixError("derived matrix tree is not closed")
    require_secret_free(root)
    return manifest


__all__ = [
    "DerivedMatrixManifest",
    "generate_derived_matrix",
    "verify_derived_matrix",
]
