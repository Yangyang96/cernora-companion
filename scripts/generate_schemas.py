"""Regenerate checked-in companion JSON Schemas from strict public models."""

from __future__ import annotations

import json
from pathlib import Path

from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.export import CompletedExportManifest
from cernora_reference_workflow.report import RunReport
from cernora_reference_workflow.run_plan import RunPlan

ROOT = Path(__file__).resolve().parents[1]
SchemaModel = type[ExperimentSpec] | type[CompletedExportManifest] | type[RunReport] | type[RunPlan]


def schema_bytes(name: str, model: SchemaModel) -> bytes:
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://cernora.example/schemas/{name}"
    return (json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_schema(
    name: str,
    model: SchemaModel,
) -> None:
    (ROOT / "schemas" / name).write_bytes(schema_bytes(name, model))


def main() -> int:
    (ROOT / "schemas").mkdir(exist_ok=True)
    write_schema("experiment-spec-v1.schema.json", ExperimentSpec)
    write_schema("completed-export-v1.schema.json", CompletedExportManifest)
    write_schema("run-report-v1.schema.json", RunReport)
    write_schema("run-plan-v1.schema.json", RunPlan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
