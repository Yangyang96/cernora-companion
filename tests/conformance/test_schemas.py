from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator

from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    materialize_comparison_plan,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    ControlledExperimentSpecV2,
    materialize_controlled_experiment_spec,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec, materialize_experiment_spec
from cernora_reference_workflow.export import CompletedExportManifest
from cernora_reference_workflow.report import RunReport

from ..unit.test_comparison_plan import valid_payload as valid_comparison_payload
from ..unit.test_controlled_experiment_spec import valid_payload as valid_controlled_spec_payload
from ..unit.test_controlled_run_plan import valid_payload as valid_controlled_run_plan_payload
from ..unit.test_experiment_spec import valid_payload

ROOT = Path(__file__).resolve().parents[2]


def checked_in_schema(name: str) -> dict[str, object]:
    payload = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("checked-in schema must be a JSON object")
    return cast(dict[str, object], payload)


def generated_schema(
    name: str,
    model: type[
        ExperimentSpec
        | CompletedExportManifest
        | RunReport
        | ControlledExperimentSpecV2
        | ControlledRunPlanV2
        | ComparisonPlanV1
    ],
) -> dict[str, object]:
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://cernora.example/schemas/{name}"
    return schema


def test_experiment_schema_is_current_and_accepts_the_strict_model() -> None:
    name = "experiment-spec-v1.schema.json"
    schema = checked_in_schema(name)
    assert schema == generated_schema(name, ExperimentSpec)
    Draft202012Validator.check_schema(schema)
    instance = materialize_experiment_spec(valid_payload()).model_dump(mode="json")
    Draft202012Validator(schema).validate(instance)


def test_completed_export_schema_is_current_and_strict() -> None:
    name = "completed-export-v1.schema.json"
    schema = checked_in_schema(name)
    assert schema == generated_schema(name, CompletedExportManifest)
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False


def test_run_report_schema_is_current_and_strict() -> None:
    name = "run-report-v1.schema.json"
    schema = checked_in_schema(name)
    assert schema == generated_schema(name, RunReport)
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False


def test_controlled_experiment_spec_v2_schema_is_current_and_strict() -> None:
    name = "controlled-experiment-spec-v2.schema.json"
    schema = checked_in_schema(name)
    assert schema == generated_schema(name, ControlledExperimentSpecV2)
    Draft202012Validator.check_schema(schema)
    instance = materialize_controlled_experiment_spec(valid_controlled_spec_payload()).model_dump(
        mode="json"
    )
    Draft202012Validator(schema).validate(instance)


def test_controlled_run_plan_v2_schema_is_current_and_strict() -> None:
    name = "controlled-run-plan-v2.schema.json"
    schema = checked_in_schema(name)
    assert schema == generated_schema(name, ControlledRunPlanV2)
    Draft202012Validator.check_schema(schema)
    instance = materialize_controlled_run_plan(valid_controlled_run_plan_payload()).model_dump(
        mode="json"
    )
    Draft202012Validator(schema).validate(instance)


def test_comparison_plan_v1_schema_is_current_and_strict() -> None:
    name = "comparison-plan-v1.schema.json"
    schema = checked_in_schema(name)
    assert schema == generated_schema(name, ComparisonPlanV1)
    Draft202012Validator.check_schema(schema)
    run_plan = materialize_controlled_run_plan(valid_controlled_run_plan_payload())
    instance = materialize_comparison_plan(valid_comparison_payload(run_plan)).model_dump(
        mode="json"
    )
    Draft202012Validator(schema).validate(instance)
