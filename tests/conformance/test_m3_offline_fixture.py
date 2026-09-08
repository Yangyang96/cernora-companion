from __future__ import annotations

import json
from pathlib import Path

import pytest
from cernora import BatchInput
from pydantic import ValidationError

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.comparison_plan import ComparisonPlanV1
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "examples/m3-offline"
# Core wheel digest frozen inside the historical m3-offline evidence (0.3.0-era core,
# predating the ACCEPTED_CORE_0_1_4_WHEEL_SHA256 authority).
_FROZEN_M3_CORE_WHEEL_SHA256 = "5b847837b7182b3ece8054eb5187fde4f835582787b406ea4a7f2f8bd2987a4c"


def test_m3_offline_fixture_is_frozen_codex_era_evidence() -> None:
    """The frozen trio binds itself; current pi-era contracts reject its run plan.

    The era boundary decision (see tests/unit/test_runtime_era_boundary.py) froze the
    historical Codex-era m3-offline trio as in-repo evidence. The current strict
    contracts therefore reject the historical run plan, while the era-neutral
    BatchInput and ComparisonPlan records still load canonically and agree with the
    frozen run plan identity and wheel authorities.
    """

    frozen_plan_bytes = (FIXTURE / "controlled-run-plan.json").read_bytes()
    frozen_plan = json.loads(frozen_plan_bytes)
    assert frozen_plan_bytes == canonical_json_bytes(frozen_plan)
    with pytest.raises(ValidationError, match="cernora-reference-harbor-pi"):
        ControlledRunPlanV2.from_file(FIXTURE / "controlled-run-plan.json")

    comparison = ComparisonPlanV1.from_file(FIXTURE / "comparison-plan.json")
    batch_bytes = (FIXTURE / "batch-input.json").read_bytes()
    batch = BatchInput.model_validate_json(batch_bytes)

    assert batch_bytes == canonical_json_bytes(batch.model_dump(mode="json"))
    assert batch.run_plan_id == comparison.source_run_plan_id == frozen_plan["run_plan_id"]
    assert batch.companion_version == frozen_plan["companion_version"]
    assert all(
        spec["cernora"]["wheel_sha256"] == _FROZEN_M3_CORE_WHEEL_SHA256
        for spec in frozen_plan["experiment_specs"]
    )


def test_current_offline_fixture_binds_the_current_strict_contracts() -> None:
    from cernora_reference_workflow.comparison_plan import materialize_comparison_plan
    from cernora_reference_workflow.controlled_run_plan import materialize_controlled_run_plan
    from tests.unit.test_comparison_input import _lifecycle_batch
    from tests.unit.test_comparison_plan import valid_payload as comparison_payload
    from tests.unit.test_controlled_run_plan import valid_payload as run_payload

    fixture = ROOT / "examples/p4-offline"
    plan = ControlledRunPlanV2.from_file(fixture / "controlled-run-plan.json")
    comparison = ComparisonPlanV1.from_file(fixture / "comparison-plan.json")
    batch_bytes = (fixture / "batch-input.json").read_bytes()
    batch = BatchInput.model_validate_json(batch_bytes)
    comparison.validate_run_plan(plan)
    assert batch.run_plan_id == comparison.source_run_plan_id == plan.run_plan_id
    assert plan == materialize_controlled_run_plan(run_payload())
    assert comparison == materialize_comparison_plan(comparison_payload(plan))
    assert batch == _lifecycle_batch(plan)
    assert batch_bytes == canonical_json_bytes(batch.model_dump(mode="json"))
    assert all(trial.attempts[0].lifecycle is not None for trial in batch.trials)
