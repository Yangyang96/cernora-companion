from __future__ import annotations

from pathlib import Path

from cernora_reference_workflow.native_acceptance import build_m1_native_acceptance_plan
from cernora_reference_workflow.run_plan import RunPlan

ROOT = Path(__file__).resolve().parents[2]


def test_native_acceptance_plan_matches_the_canonical_example() -> None:
    plan = build_m1_native_acceptance_plan(ROOT)
    example = ROOT / "examples/priority4-m1-native-acceptance.json"
    assert example.read_bytes() == plan.canonical_bytes()
    assert RunPlan.from_file(example) == plan


def test_native_acceptance_plan_freezes_the_approved_exit_matrix() -> None:
    plan = build_m1_native_acceptance_plan(ROOT)
    assert [item.case_id for item in plan.cases] == [
        "tiny-calculator-v1",
        "tiny-calculator-v2",
    ]
    assert [item.configuration_id for item in plan.configurations] == [
        "normal-policy",
        "short-timeout-policy",
    ]
    assert len({item.experiment_id for item in plan.cells}) == 4
    assert [item.slot_index for item in plan.expand_trial_slots()] == list(range(1, 13))
    assert [item.repetition for item in plan.expand_trial_slots()] == [1, 2, 3] * 4
    assert plan.planned_trial_count == 12
    assert plan.worst_case_attempt_count == 24
    assert plan.execution.max_attempt_count == 24
    assert plan.execution.max_total_wall_time_seconds == 7200
