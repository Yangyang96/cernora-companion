"""Explicit single-attempt commands, also exposed under experiment skill."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cernora_reference_workflow.common import canonical_json_bytes, load_json_bytes
from cernora_reference_workflow.skill_capture.contracts import SkillPlan, digest, replay


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="skill_command", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("plan", type=Path)
    capture = commands.add_parser("capture")
    capture.add_argument("plan", type=Path)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--auth", type=Path, required=True)
    capture.add_argument("--accept-plan-sha256", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("export", type=Path)
    evaluate.add_argument("--plan", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    diagnostic = commands.add_parser("diagnose")
    diagnostic.add_argument("export", type=Path)
    diagnostic.add_argument("--plan", type=Path, required=True)
    diagnostic.add_argument("--output", type=Path, required=True)
    freeze = commands.add_parser("freeze-comparison")
    freeze.add_argument("plan", type=Path)
    freeze.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("freeze", type=Path)
    compare.add_argument("--sources", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    tool = commands.add_parser("replay")
    tool.add_argument("plan", type=Path)
    tool.add_argument("argv")


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.skill_command in {"freeze-comparison", "compare"}:
        from cernora_reference_workflow.skill_capture.comparison import (
            compare_exports,
            freeze_comparison,
            parse_plan,
        )

        if args.skill_command == "freeze-comparison":
            frozen = freeze_comparison(parse_plan(load_json_bytes(args.plan.read_bytes())))
            with args.output.open("xb") as handle:
                handle.write(canonical_json_bytes(frozen))
            return {"freeze": str(args.output), "plan_sha256": frozen["plan_sha256"]}
        report = compare_exports(args.freeze, args.sources, args.output)
        return {"conclusion": report["conclusion"], "report": str(args.output / "report.md")}
    plan = SkillPlan.read(args.plan)
    if args.skill_command == "diagnose":
        from cernora_reference_workflow.skill_capture.diagnostics import diagnose_export

        report = diagnose_export(plan, args.export, args.output)
        return {"summary": report["summary"], "report": str(args.output / "diagnostics.md")}
    if args.skill_command == "inspect":
        return {
            "plan_sha256": plan.sha256,
            "case_id": plan.case_id,
            "provider": plan.provider,
            "model": plan.model,
            "base_url": plan.base_url,
            "invocation": plan.invocation,
            "skill_files": {k: digest(v.encode()) for k, v in plan.skill_files.items()},
            "snapshots": len(plan.objects),
            "max_requests": plan.max_requests,
            "max_tool_calls": plan.max_tool_calls,
            "max_output_tokens": plan.max_output_tokens,
            "max_request_bytes": plan.max_request_bytes,
            "timeout_seconds": plan.timeout_seconds,
            "billing_cap": "unavailable",
        }
    if args.skill_command == "replay":
        argv = load_json_bytes(args.argv.encode())
        if not isinstance(argv, list) or any(not isinstance(a, str) for a in argv):
            raise ValueError("argv must be a string array")
        return replay(plan, argv)
    if args.skill_command == "capture":
        from cernora_reference_workflow.skill_capture.runtime import capture

        capture(args.plan, args.output, args.auth, args.accept_plan_sha256)
        return {"export": str(args.output), "plan_sha256": plan.sha256}
    from cernora_reference_workflow.skill_capture.evaluation import evaluate_export

    return evaluate_export(plan, args.export, args.output)


def main() -> None:
    parser = argparse.ArgumentParser()
    configure(parser)
    print(canonical_json_bytes(dispatch(parser.parse_args())).decode(), end="")


if __name__ == "__main__":
    main()
