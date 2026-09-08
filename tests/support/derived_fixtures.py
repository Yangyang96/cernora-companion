"""Deterministic, explicitly labeled mutations derived from a valid frozen export."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

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
    materialize_experiment_spec,
)
from cernora_reference_workflow.export import verify_completed_export
from cernora_reference_workflow.offline import verify_workflow_binding
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.secrets import require_secret_free

MutationName = Literal[
    "missing-required-artifact",
    "content-digest-mismatch",
    "profile-authority-mismatch",
    "test-runner-authority-mismatch",
]


class DerivedFixtureError(ValueError):
    """A derived fixture cannot be produced or verified deterministically."""


class DerivedFile(StrictContract):
    path: NonEmpty
    byte_length: Annotated[StrictInt, Field(ge=0)]
    sha256: Digest

    @model_validator(mode="after")
    def validate_path(self) -> DerivedFile:
        validate_relative_path(self.path)
        if self.path == "fixture.json":
            raise ValueError("fixture.json cannot recursively list itself")
        return self


class DerivedFixtureManifest(StrictContract):
    schema_version: Literal["cernora.reference.derived-fixture/v1"]
    fixture_id: Digest
    recipe_version: Literal["1"]
    mutation: MutationName
    expected_evaluation: Literal["inconclusive"]
    source_export_sha256: Digest
    source_experiment_spec_sha256: Digest
    files: Annotated[tuple[DerivedFile, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_identity(self) -> DerivedFixtureManifest:
        paths = tuple(item.path for item in self.files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("derived fixture file index must be uniquely sorted")
        expected = sha256_bytes(
            canonical_json_bytes(self.model_dump(mode="json", exclude={"fixture_id"}))
        )
        if self.fixture_id != expected:
            raise ValueError("derived fixture identity mismatch")
        return self


def _copy_tree(source: Path, destination: Path) -> None:
    for relative, path in closed_regular_tree(source).items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def _write_alternate_profile_spec(spec: ExperimentSpec, path: Path) -> ExperimentSpec:
    payload = spec.model_dump(mode="json", exclude={"experiment_id"})
    profile = payload["profile"]
    if not isinstance(profile, dict):
        raise DerivedFixtureError("ExperimentSpec Profile payload is malformed")
    profile["authority_sha256"] = (
        "f" * 64 if spec.profile.authority_sha256 != "f" * 64 else "e" * 64
    )
    alternate = materialize_experiment_spec(payload)
    path.write_bytes(alternate.canonical_bytes())
    return alternate


def derive_invalid_fixture(
    *,
    source_export: Path,
    source_spec: ExperimentSpec,
    mutation: MutationName,
    destination: Path,
) -> DerivedFixtureManifest:
    if destination.exists() or destination.is_symlink():
        raise DerivedFixtureError("derived fixture destination must not already exist")
    if not destination.parent.is_dir():
        raise DerivedFixtureError("derived fixture parent must already exist")
    source_manifest = verify_completed_export(source_export)
    verify_workflow_binding(source_spec, source_manifest)
    source_export_sha256 = sha256_file(source_export / "manifest.json")
    source_spec_sha256 = sha256_bytes(source_spec.canonical_bytes())

    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        export = staging / "export"
        _copy_tree(source_export, export)
        (staging / "experiment-spec.json").write_bytes(source_spec.canonical_bytes())
        if mutation == "missing-required-artifact":
            (export / "tests/test-results.json").unlink()
        elif mutation == "content-digest-mismatch":
            (export / "tests/stdout.txt").write_bytes(b"deterministic digest mismatch\n")
        elif mutation == "profile-authority-mismatch":
            alternate = _write_alternate_profile_spec(
                source_spec,
                staging / "experiment-spec.json",
            )
            manifest_payload = load_json_file(export / "manifest.json")
            if not isinstance(manifest_payload, dict):
                raise DerivedFixtureError("source export manifest is malformed")
            manifest_payload["experiment_id"] = alternate.experiment_id
            (export / "manifest.json").write_bytes(canonical_json_bytes(manifest_payload))
        else:
            manifest_payload = load_json_file(export / "manifest.json")
            if not isinstance(manifest_payload, dict):
                raise DerivedFixtureError("source export manifest is malformed")
            manifest_payload["test_authority_sha256"] = "f" * 64
            (export / "manifest.json").write_bytes(canonical_json_bytes(manifest_payload))

        files = tuple(
            DerivedFile(
                path=relative,
                byte_length=path.stat().st_size,
                sha256=sha256_file(path),
            )
            for relative, path in closed_regular_tree(staging).items()
        )
        payload = {
            "schema_version": "cernora.reference.derived-fixture/v1",
            "recipe_version": "1",
            "mutation": mutation,
            "expected_evaluation": "inconclusive",
            "source_export_sha256": source_export_sha256,
            "source_experiment_spec_sha256": source_spec_sha256,
            "files": [item.model_dump(mode="json") for item in files],
        }
        payload["fixture_id"] = sha256_bytes(canonical_json_bytes(payload))
        manifest = DerivedFixtureManifest.model_validate(payload)
        (staging / "fixture.json").write_bytes(
            canonical_json_bytes(manifest.model_dump(mode="json"))
        )
        require_secret_free(staging)
        verify_derived_fixture(staging)
        atomic_publish_directory(staging, destination)
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def verify_derived_fixture(root: Path) -> DerivedFixtureManifest:
    files = closed_regular_tree(root)
    payload = load_json_file(root / "fixture.json")
    if not isinstance(payload, dict):
        raise DerivedFixtureError("fixture manifest must contain one JSON object")
    manifest = DerivedFixtureManifest.model_validate(payload)
    if (root / "fixture.json").read_bytes() != canonical_json_bytes(
        manifest.model_dump(mode="json")
    ):
        raise DerivedFixtureError("fixture manifest is not canonical JSON")
    observed = set(files) - {"fixture.json"}
    indexed = {entry.path for entry in manifest.files}
    if observed != indexed:
        raise DerivedFixtureError("derived fixture tree does not match its manifest")
    for entry in manifest.files:
        path = files[entry.path]
        if path.stat().st_size != entry.byte_length or sha256_file(path) != entry.sha256:
            raise DerivedFixtureError(f"derived fixture content mismatch: {entry.path}")
    require_secret_free(root)
    return manifest


def plant_fake_secret_and_require_rejection(
    *,
    source_export: Path,
    staging_parent: Path,
) -> None:
    """Plant a provably fake recognizable credential; success is always an error."""

    verify_completed_export(source_export)
    staging = Path(tempfile.mkdtemp(prefix=".fake-secret.staging-", dir=staging_parent))
    try:
        _copy_tree(source_export, staging)
        (staging / "tests/stdout.txt").write_bytes(
            b"Authorization: Bearer " + b"fake_" + b"Priority3NeverLiveCredential000000000000\n"
        )
        require_secret_free(staging)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    raise DerivedFixtureError("planted fake secret was not rejected")


__all__ = [
    "DerivedFixtureManifest",
    "derive_invalid_fixture",
    "plant_fake_secret_and_require_rejection",
    "verify_derived_fixture",
]
