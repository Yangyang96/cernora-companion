"""Evidence-linked observations; no causal verdicts or scoring repairs."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from cernora import read_evaluation_report, read_imported_evaluation

from cernora_reference_workflow.common import ContractError, canonical_json_bytes, load_json_bytes
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.skill_capture.audit import audit
from cernora_reference_workflow.skill_capture.contracts import SkillPlan, digest, verify_export
from cernora_reference_workflow.skill_capture.evaluation import (
    SkillWorkflowProfile,
    evaluate_export,
)
from cernora_reference_workflow.skill_capture.runtime import publish_export


def _diagnose_export(
    plan: SkillPlan, source: Path, output: Path, *, profile: SkillWorkflowProfile | None = None
) -> dict[str, Any]:
    selected, files = verify_export(source)
    if selected != plan:
        raise ContractError("diagnostic source differs from selected Plan")
    summary = audit(plan, files)
    result = evaluate_export(plan, source, output, profile=profile)
    if verify_export(source)[1] != files:
        raise ContractError("native source changed during diagnosis")
    publish_export(output / "native", files, plan)
    chosen = profile or SkillWorkflowProfile(plan)
    receipt = read_imported_evaluation(output / "evaluation", chosen)
    report = read_evaluation_report(output / "evaluation", chosen)
    findings: list[dict[str, Any]] = []

    def add(
        code: str,
        layer: str,
        observation: str,
        file: str,
        pointer: str,
        *,
        hypothesis: str | None = None,
    ) -> None:
        findings.append(
            dict(
                code=code,
                layer=layer,
                observation=observation,
                source=dict(file=file, sha256=digest(files[file]), pointer=pointer),
                hypothesis=hypothesis,
                causal_status="not_established",
            )
        )

    if not summary["complete"]:
        add(
            "completion_unavailable",
            "infrastructure",
            "Complete terminal evidence is unavailable.",
            "process.json",
            "",
            hypothesis="Inspect process termination and provider responses.",
        )
    if summary["skill_loading"] != "observed":
        add(
            "loading_unobserved",
            "loading",
            "No full Skill body delivery was observed.",
            "requests.jsonl",
            "",
            hypothesis="Inspect discovery and read ranges before causal attribution.",
        )
    if not summary["usage_complete"]:
        add(
            "usage_incomplete",
            "accounting",
            "At least one request has no verified usage receipt.",
            "requests.jsonl",
            "",
        )
    for i, tool in enumerate(summary["tools"]):
        if tool["exit_code"] != 0:
            add(
                "tool_error",
                "tool",
                f"Invocation {tool['id']} returned exit {tool['exit_code']}.",
                "tools.jsonl",
                f"/lines/{i + 1}",
                hypothesis="Inspect arguments and error before claiming a Skill defect.",
            )
    terminal_line = max(
        (
            i + 1
            for i, line in enumerate(files["events.jsonl"].splitlines())
            if (event := load_json_bytes(line)).get("type") == "message_end"
            and event.get("message", {}).get("role") == "assistant"
        ),
        default=0,
    )
    raw = summary["raw_answer"]
    answer: Any = None
    parsed = False
    if summary["complete"]:
        try:
            answer = load_json_bytes(raw.encode())
            parsed = True
        except (ValueError, AttributeError):
            add(
                "answer_not_json",
                "answer",
                "The unmodified answer is not a single JSON value.",
                "events.jsonl",
                f"/lines/{terminal_line}/message/content",
                hypothesis="Test a stricter output instruction in a new frozen comparison.",
            )
        if parsed:
            if not isinstance(answer, dict) or set(answer) != {"facts", "evidence_ids"}:
                add(
                    "answer_shape",
                    "answer",
                    "Answer fields differ from the declared schema.",
                    "events.jsonl",
                    f"/lines/{terminal_line}/message/content",
                )
            else:
                expected = {f.field: f.expected for f in plan.facts}
                if canonical_json_bytes(answer["facts"]) != canonical_json_bytes(expected):
                    add(
                        "fact_mismatch",
                        "answer",
                        "Reported facts differ from frozen source-bound expectations.",
                        "events.jsonl",
                        f"/lines/{terminal_line}/message/content",
                    )
                if result["outcome"] == "fail":
                    add(
                        "task_constraint_failed",
                        "task",
                        "Task metric failed; inspect facts, citations, and query dependency.",
                        "events.jsonl",
                        f"/lines/{terminal_line}/message/content",
                    )
    diagnostic = dict(
        schema_version="cernora.reference.skill-diagnostic/v1",
        summary=result,
        evaluation_receipt=receipt.model_dump(mode="json"),
        evaluation_report=None if report is None else report.model_dump(mode="json"),
        findings=findings,
        steps=[
            dict(
                invocation_id=t["id"],
                request_index=t["request_index"],
                tool=t["tool"],
                arguments=t["args"],
                exit_code=t["exit_code"],
                source=dict(
                    file="tools.jsonl",
                    sha256=digest(files["tools.jsonl"]),
                    pointer=f"/lines/{i + 1}",
                ),
            )
            for i, t in enumerate(summary["tools"])
        ],
        source_files={name: digest(raw) for name, raw in files.items()},
        limitations=[
            "Observations are not causal attribution.",
            "Tool exploration errors do not alone fail a successful task.",
            "Runtime cost estimates are not verified billing.",
        ],
    )
    (output / "diagnostics.json").write_bytes(canonical_json_bytes(diagnostic))
    lines = [
        "# Skill diagnostics",
        "",
        f"Task outcome: {result['outcome']}. Skill loading: {result['skill_loading']}.",
        "",
        "[Metrics and evidence references](evaluation/evaluation-report.json).",
        "",
        "## Observations",
        "",
    ]
    lines.extend(
        f"- `{f['code']}` ({f['layer']}): {f['observation']} "
        f"Source: [{f['source']['file']}](native/{f['source']['file']}) "
        f"`{f['source']['pointer']}`."
        + (f" Hypothesis to test: {f['hypothesis']}" if f["hypothesis"] else "")
        for f in findings
    )
    lines.extend(
        [
            "",
            "[Source hashes, steps and metric evidence](diagnostics.json).",
            "",
            "No finding establishes a Skill root cause. Missing cost remains unavailable.",
            "",
        ]
    )
    (output / "diagnostics.md").write_text("\n".join(lines))
    return diagnostic


def diagnose_export(
    plan: SkillPlan, source: Path, output: Path, *, profile: SkillWorkflowProfile | None = None
) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise ContractError("diagnostic output must be new")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".skill-diagnostic-", dir=output.parent))
    staging.rmdir()
    try:
        result = _diagnose_export(plan, source, staging, profile=profile)
        atomic_publish_directory(staging, output)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)
