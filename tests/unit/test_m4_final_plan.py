from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.improvement_loop import verify_candidate_freeze
from cernora_reference_workflow.m4_final_plan import (
    M4ImageAuthoritySet,
    build_m4_final_plans,
    materialize_m4_image_authority_set,
)
from tests.unit.test_improvement_loop import (
    VISIBLE_ROOT,
    _all_task_authorities,
    _freeze_and_final,
    _reveal,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _images(case_ids: tuple[str, ...]) -> M4ImageAuthoritySet:
    return materialize_m4_image_authority_set(
        build_base_image=f"cernora-reference/codex-runtime@sha256:{_digest('base')}",
        images={
            case_id: f"cernora-reference/m4-{case_id}@sha256:{_digest(case_id)}"
            for case_id in case_ids
        },
    )


def test_final_builder_freezes_exact_nine_case_authorities(tmp_path: Path) -> None:
    freeze, pilot, _, _, manifest = _freeze_and_final(tmp_path)
    tasks = _all_task_authorities(manifest)
    images = _images(tuple(item.case.case_id for item in tasks))

    first_plan, first_comparison = build_m4_final_plans(
        tasks=tasks,
        freeze=freeze,
        image_authorities=images,
    )
    second_plan, second_comparison = build_m4_final_plans(
        tasks=tuple(reversed(tasks)),
        freeze=freeze,
        image_authorities=M4ImageAuthoritySet.from_bytes(images.canonical_bytes()),
    )

    assert first_plan.canonical_bytes() == second_plan.canonical_bytes()
    assert first_comparison.canonical_bytes() == second_comparison.canonical_bytes()
    assert len(first_plan.experiment_specs) == 18
    assert len(first_plan.expand_trial_slots()) == 54
    assert len({item.trial_slot_id for item in first_plan.expand_trial_slots()}) == 54
    assert first_plan.worst_case_attempt_count == first_plan.execution.max_attempt_count == 108
    assert first_plan.execution.max_total_wall_time_seconds == 43_200
    assert all(
        item.prompt_source == freeze.baseline_prompt_authority
        for item in first_plan.experiment_specs
        if item.configuration_id == "baseline"
    )
    assert all(
        item.prompt_source == freeze.candidate_prompt_authority
        for item in first_plan.experiment_specs
        if item.configuration_id == "candidate"
    )
    assert first_comparison.materialize_core_treatment(first_plan).changes[0].kind == (
        "prompt_instruction"
    )
    receipt = verify_candidate_freeze(
        freeze,
        pilot_package=pilot,
        run_plan=first_plan,
        comparison_plan=first_comparison,
        visible_corpus_root=VISIBLE_ROOT,
        heldout_manifest=manifest,
        reveal_receipt=_reveal(manifest, freeze),
        task_authorities=tasks,
    )
    assert receipt.run_plan_id == first_plan.run_plan_id


def test_final_builder_rejects_incomplete_image_authority(tmp_path: Path) -> None:
    freeze, _, _, _, manifest = _freeze_and_final(tmp_path)
    tasks = _all_task_authorities(manifest)
    images = _images(tuple(item.case.case_id for item in tasks))
    payload = images.model_dump(mode="json", exclude={"authority_set_id"})
    payload["images"].pop()
    payload["authority_set_id"] = "0" * 64

    with pytest.raises((ContractError, ValueError)):
        build_m4_final_plans(
            tasks=tasks,
            freeze=freeze,
            image_authorities=M4ImageAuthoritySet.model_validate(payload),
        )


def test_image_authority_set_rejects_case_name_substitution() -> None:
    case_ids = tuple(f"case-{index}" for index in range(9))
    images = _images(case_ids)
    payload = images.model_dump(mode="json")
    payload["images"][0]["image"] = f"cernora-reference/m4-another-case@sha256:{_digest('case-0')}"

    with pytest.raises(ValueError, match="bind its Case ID"):
        M4ImageAuthoritySet.model_validate(payload)
