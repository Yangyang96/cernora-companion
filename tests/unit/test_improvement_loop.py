from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from cernora import BatchFailureCodeCount, BatchInput, BatchSummary
from pydantic import ValidationError

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.controlled_experiment_spec import materialize_authority_source
from cernora_reference_workflow.improvement_loop import (
    FrozenM4Policy,
    materialize_candidate_freeze,
    materialize_development_pilot,
    select_leading_failure,
    visible_corpus_digest,
)

DIGEST = "1" * 64
DEV_CASES = ("dev-interval", "dev-ledger", "dev-query")


def pilot_inputs(
    failure_codes: tuple[BatchFailureCodeCount, ...],
) -> tuple[BatchInput, BatchSummary]:
    planned = tuple(
        SimpleNamespace(case_id=case_id, configuration_id="baseline") for case_id in DEV_CASES
    )
    batch = cast(
        BatchInput,
        SimpleNamespace(
            batch_input_id="batch-input-pilot",
            batch_input_sha256="2" * 64,
            planned_trials=planned,
        ),
    )
    summary = cast(
        BatchSummary,
        SimpleNamespace(
            summary_id="batch-summary-pilot",
            summary_sha256="3" * 64,
            batch_input_id=batch.batch_input_id,
            batch_input_sha256=batch.batch_input_sha256,
            by_case=tuple(SimpleNamespace(case_id=item) for item in DEV_CASES),
            profile_failure_codes=failure_codes,
        ),
    )
    return batch, summary


def test_leading_failure_uses_only_pilot_and_deterministic_tie_break() -> None:
    batch, summary = pilot_inputs(
        (
            BatchFailureCodeCount(
                profile_id="repair-profile",
                profile_version="1",
                code="query_escape_v1",
                count=2,
            ),
            BatchFailureCodeCount(
                profile_id="repair-profile",
                profile_version="1",
                code="interval_boundary_v1",
                count=2,
            ),
        )
    )

    leading = select_leading_failure(batch, summary, development_case_ids=DEV_CASES)

    assert leading.code == "interval_boundary_v1"
    assert leading.count == 2


def test_no_development_failure_fails_closed() -> None:
    batch, summary = pilot_inputs(())
    with pytest.raises(ContractError, match="no usable"):
        select_leading_failure(batch, summary, development_case_ids=DEV_CASES)


def test_candidate_freeze_binds_offline_pilot_seal_policy_and_one_prompt_change() -> None:
    codes = (
        BatchFailureCodeCount(
            profile_id="repair-profile",
            profile_version="1",
            code="interval_boundary_v1",
            count=2,
        ),
    )
    batch, summary = pilot_inputs(codes)
    pilot = materialize_development_pilot(
        run_plan_id=DIGEST,
        batch_input=batch,
        summary=summary,
        development_case_ids=DEV_CASES,
    )
    leading = select_leading_failure(batch, summary, development_case_ids=DEV_CASES)
    baseline = materialize_authority_source("baseline-prompt", {"text": "Repair the task."})
    candidate = materialize_authority_source(
        "candidate-prompt", {"text": "Inspect interval boundaries, then repair the task."}
    )
    policy = FrozenM4Policy(
        schema_version="cernora.reference.m4-policy/v1",
        primary_metric="reliable_success_rate",
        practical_threshold_basis_points=1000,
        evaluation_validity_max_adverse_basis_points=0,
        protected_path_failure_max_adverse_basis_points=0,
        regression_reliable_success_max_adverse_basis_points=1000,
        improved_requires_interval_above_zero=True,
        improved_requires_all_hard_guardrails=True,
    )
    freeze = materialize_candidate_freeze(
        {
            "schema_version": "cernora.reference.candidate-freeze/v1",
            "pilot": pilot.model_dump(mode="json"),
            "leading_failure": leading.model_dump(mode="json"),
            "treatment_kind": "prompt_instruction",
            "baseline_prompt_authority": baseline.model_dump(mode="json"),
            "candidate_prompt_authority": candidate.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "visible_corpus_sha256": "4" * 64,
            "heldout_seal_manifest_id": "heldout-seal-1",
            "heldout_seal_manifest_sha256": "5" * 64,
        }
    )

    assert freeze.pilot.prohibited_from_final_live_batch is True
    assert freeze.treatment_kind == "prompt_instruction"
    assert freeze.candidate_freeze_id == f"candidate-freeze-{freeze.candidate_freeze_sha256}"

    with pytest.raises(ValidationError, match="substantive"):
        type(freeze).model_validate(
            freeze.model_copy(update={"candidate_prompt_authority": baseline}).model_dump(
                mode="json"
            )
        )


def test_visible_corpus_digest_is_content_identified(tmp_path: Path) -> None:
    source = tmp_path / "visible"
    shutil.copytree(Path("examples/m4-visible"), source)
    first = visible_corpus_digest(source)
    (source / "dev-interval-merge" / "solution.py").write_text(
        "def merge(value): return value\n", encoding="utf-8"
    )

    assert visible_corpus_digest(source) != first
