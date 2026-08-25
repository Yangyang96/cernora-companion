from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.derived_matrix import (
    DerivedMatrixError,
    generate_derived_matrix,
    verify_derived_matrix,
)

from .test_derived_fixtures import _source


def test_derived_matrix_is_closed_bound_and_immutable(tmp_path: Path) -> None:
    spec, source = _source(tmp_path)
    destination = tmp_path / "matrix"
    manifest = generate_derived_matrix(
        source_export=source,
        source_spec=spec,
        destination=destination,
    )

    assert verify_derived_matrix(destination) == manifest
    assert tuple(item.mutation for item in manifest.fixtures) == (
        "missing-required-artifact",
        "content-digest-mismatch",
        "profile-authority-mismatch",
        "test-runner-authority-mismatch",
    )
    with pytest.raises((DerivedMatrixError, ValueError)):
        generate_derived_matrix(
            source_export=source,
            source_spec=spec,
            destination=destination,
        )


def test_derived_matrix_rejects_post_publication_mutation(tmp_path: Path) -> None:
    spec, source = _source(tmp_path)
    destination = tmp_path / "matrix"
    generate_derived_matrix(
        source_export=source,
        source_spec=spec,
        destination=destination,
    )
    target = destination / "fixtures/content-digest-mismatch/export/tests/stdout.txt"
    target.write_bytes(target.read_bytes() + b"changed\n")

    with pytest.raises((DerivedMatrixError, ValueError)):
        verify_derived_matrix(destination)
