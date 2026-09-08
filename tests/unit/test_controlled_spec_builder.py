from __future__ import annotations

import hashlib

import pytest

from cernora_reference_workflow.controlled_spec_builder import (
    M4ImageAuthoritySet,
    materialize_m4_image_authority_set,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _images(case_ids: tuple[str, ...]) -> M4ImageAuthoritySet:
    return materialize_m4_image_authority_set(
        build_base_image=f"cernora-reference/pi-runtime@sha256:{_digest('base')}",
        images={
            case_id: f"cernora-reference/m4-{case_id}@sha256:{_digest(case_id)}"
            for case_id in case_ids
        },
    )


def test_image_authority_set_rejects_case_name_substitution() -> None:
    case_ids = tuple(f"case-{index}" for index in range(9))
    images = _images(case_ids)
    payload = images.model_dump(mode="json")
    payload["images"][0]["image"] = f"cernora-reference/m4-another-case@sha256:{_digest('case-0')}"

    with pytest.raises(ValueError, match="bind its Case ID"):
        M4ImageAuthoritySet.model_validate(payload)


def test_image_authority_set_rejects_incomplete_case_set() -> None:
    images = _images(tuple(f"case-{index}" for index in range(9)))
    payload = images.model_dump(mode="json")
    payload["images"].pop()
    with pytest.raises(ValueError):
        M4ImageAuthoritySet.model_validate(payload)
