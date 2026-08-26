from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cernora_reference_workflow.controlled_batch_summary import (
    build_controlled_batch_summary,
    normalize_controlled_execution,
    publish_controlled_batch_summary,
)
from cernora_reference_workflow.controlled_run_plan import materialize_controlled_run_plan
from cernora_reference_workflow.controlled_runner import GIBIBYTE, execute_controlled_run
from tests.unit.test_controlled_run_plan import valid_payload
from tests.unit.test_controlled_runner import FakeExecutor


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_controlled_normalizer_preserves_complete_slots_and_attempt_chains(
    tmp_path: Path,
) -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    execution = execute_controlled_run(
        plan,
        FakeExecutor(retry_first=True),
        workspace=tmp_path,
        nonce=digest("normalizer"),
        clock=lambda: 0.0,
        sleeper=lambda _: None,
        disk_free=lambda _: 20 * GIBIBYTE,
    )

    batch = normalize_controlled_execution(execution, plan)
    summary = build_controlled_batch_summary(execution, plan)

    assert batch.planned_trial_count == 12
    assert batch.attempt_count == 13
    assert len(batch.trials[0].attempts) == 2
    assert summary.overall.outcomes.infrastructure_unavailable == 12
    assert summary.overall.reliable_success_rate.value == 0


def test_controlled_summary_publishes_strict_core_package(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    execution = execute_controlled_run(
        plan,
        FakeExecutor(),
        workspace=tmp_path,
        nonce=digest("publish"),
        clock=lambda: 0.0,
        sleeper=lambda _: None,
        disk_free=lambda _: 20 * GIBIBYTE,
    )
    output = tmp_path / "summary"

    summary = publish_controlled_batch_summary(execution, plan, output)

    assert summary == build_controlled_batch_summary(execution, plan)
    assert (output / "batch-input.json").is_file()
    assert (output / "digests.json").is_file()


def test_normalizer_rejects_execution_bound_to_another_plan(tmp_path: Path) -> None:
    plan = materialize_controlled_run_plan(valid_payload())
    execution = execute_controlled_run(
        plan,
        FakeExecutor(),
        workspace=tmp_path,
        nonce=digest("other-plan"),
        clock=lambda: 0.0,
        sleeper=lambda _: None,
        disk_free=lambda _: 20 * GIBIBYTE,
    )
    changed = valid_payload()
    execution_policy = changed["execution"]
    assert isinstance(execution_policy, dict)
    execution_policy["max_total_wall_time_seconds"] = 43199
    other = materialize_controlled_run_plan(changed)

    with pytest.raises(ValueError, match="another RunPlan"):
        normalize_controlled_execution(execution, other)
