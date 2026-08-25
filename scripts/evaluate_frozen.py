"""Explicit offline adapt, evaluate, and strict-reload command surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cernora import (
    CompletedExport,
    evaluate_imported_case,
    import_evidence_bundle_v2,
    read_imported_evaluation,
)

from cernora_reference_workflow.adapter import ReferenceCodingAdapter
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.export import verify_completed_export
from cernora_reference_workflow.offline import verify_workflow_binding
from cernora_reference_workflow.profile import create_profile


def _adapt(args: argparse.Namespace) -> dict[str, object]:
    spec = ExperimentSpec.from_file(args.spec)
    manifest = verify_completed_export(args.export)
    verify_workflow_binding(spec, manifest)
    profile = create_profile()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    adapted = ReferenceCodingAdapter(profile).adapt(
        CompletedExport(root=args.export),
        args.output,
    )
    return {"bundle": adapted.bundle_path.as_posix(), "status": "adapted"}


def _evaluate(args: argparse.Namespace) -> dict[str, object]:
    profile = create_profile()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import_output = args.output.parent / "imported"
    import_evidence_bundle_v2(
        profile=profile,
        bundle_path=args.bundle,
        output=import_output,
    )
    receipt = evaluate_imported_case(profile, import_output, args.output)
    return {
        "case_outcome": receipt.case_outcome,
        "evaluation_id": receipt.evaluation_id,
        "status": "evaluated",
    }


def _reload(args: argparse.Namespace) -> dict[str, object]:
    receipt = read_imported_evaluation(args.output, create_profile())
    return {
        "case_outcome": receipt.case_outcome,
        "evaluation_id": receipt.evaluation_id,
        "status": "strictly-reloaded",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    adapt = subparsers.add_parser("adapt")
    adapt.add_argument("--spec", type=Path, required=True)
    adapt.add_argument("--export", type=Path, required=True)
    adapt.add_argument("--output", type=Path, required=True)
    adapt.set_defaults(handler=_adapt)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--bundle", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.set_defaults(handler=_evaluate)
    reload_parser = subparsers.add_parser("reload")
    reload_parser.add_argument("--output", type=Path, required=True)
    reload_parser.set_defaults(handler=_reload)
    args = parser.parse_args()
    payload = args.handler(args)
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
