from __future__ import annotations

from pathlib import Path

from cernora import BatchInput

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.comparison_plan import ComparisonPlanV1
from cernora_reference_workflow.controlled_experiment_spec import (
    ACCEPTED_CORE_0_1_4_WHEEL_SHA256,
)
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "examples/m3-offline"


def test_m3_offline_fixture_strictly_binds_current_authorities() -> None:
    run_plan = ControlledRunPlanV2.from_file(FIXTURE / "controlled-run-plan.json")
    comparison = ComparisonPlanV1.from_file(FIXTURE / "comparison-plan.json")
    batch_bytes = (FIXTURE / "batch-input.json").read_bytes()
    batch = BatchInput.model_validate_json(batch_bytes)

    comparison.validate_run_plan(run_plan)
    assert batch_bytes == canonical_json_bytes(batch.model_dump(mode="json"))
    assert batch.run_plan_id == run_plan.run_plan_id
    assert batch.companion_version == run_plan.companion_version
    assert all(
        spec.cernora.wheel_sha256 == ACCEPTED_CORE_0_1_4_WHEEL_SHA256
        for spec in run_plan.experiment_specs
    )
    assert [
        (
            item.slot_index,
            item.trial_slot_id,
            item.case_id,
            item.configuration_id,
            item.experiment_id,
            item.repetition,
        )
        for item in batch.planned_trials
    ] == [
        (
            item.slot_index,
            item.trial_slot_id,
            item.case_id,
            item.configuration_id,
            item.experiment_id,
            item.repetition,
        )
        for item in run_plan.expand_trial_slots()
    ]
