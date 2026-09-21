"""Pure completed-export Adapter and explicit multi-source Profile."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from cernora import (
    AdaptedBundle,
    Artifact,
    AuthorityBoundImportPackageV2,
    CaseProfile,
    CompletedExport,
    Evidence,
    EvidenceBundleV2,
    Failure,
    MetricBinding,
    MetricContext,
    MetricDefinition,
    MetricPlan,
    ProfileAssessment,
    ProfileEvaluationContext,
    ResultRecord,
    Score,
    ToolAction,
    ToolCalls,
    ToolSelection,
    evaluate_imported_case,
    external_producer_identity,
    import_evidence_bundle_v2,
    read_evaluation_report,
    read_imported_evaluation,
)

from cernora_reference_workflow.common import ContractError, canonical_json_bytes, load_json_bytes
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.skill_capture.audit import audit
from cernora_reference_workflow.skill_capture.contracts import SkillPlan, digest, verify_export


class SnapshotTask:
    definition = MetricDefinition(
        metric_id="task_outcome", metric_version="1.0.0", value_type="boolean"
    )

    def validate_parameters(self, parameters_json: str) -> None:
        SkillPlan.model_validate_json(parameters_json)

    def evaluate(self, context: MetricContext, parameters_json: str) -> ResultRecord:
        plan = SkillPlan.model_validate_json(parameters_json)
        if context.terminal_artifact is None:
            return ResultRecord(
                id="task_outcome",
                version="agent.evaluator.result-record/v1",
                role="diagnostic",
                value=None,
                value_type="boolean",
                validity="unavailable",
                failure_reason="terminal_evidence_unavailable",
                evidence_refs=(context.receipt,),
                unit=None,
                direction=None,
            )
        wrapper_bytes, ref = context.artifact(context.terminal_artifact)
        wrapper = load_json_bytes(wrapper_bytes)
        if not isinstance(wrapper, dict):
            raise ContractError("normalized answer must be an object")
        # The original answer is never replaced with a reference-derived projection.
        raw = wrapper["raw_answer"]
        matched = False
        try:
            if not isinstance(raw, str):
                raise ContractError("missing raw answer")
            answer = load_json_bytes(raw.encode())
            expected = {f.field: f.expected for f in plan.facts}
            if not isinstance(answer, dict) or set(answer) != {"facts", "evidence_ids"}:
                raise ContractError("answer contract mismatch")
            citations = answer["evidence_ids"]
            if (
                not isinstance(citations, list)
                or any(not isinstance(c, str) for c in citations)
                or len(citations) != len(set(citations))
            ):
                raise ContractError("answer citations invalid")
            tools = wrapper["capture"]["tools"]
            successful = {
                t["id"]: t for t in tools if t["tool"] == plan.tool_name and t["exit_code"] == 0
            }
            records = {
                identity: load_json_bytes(t["stdout"].encode())
                for identity, t in successful.items()
            }
            matched = (
                canonical_json_bytes(answer["facts"]) == canonical_json_bytes(expected)
                and set(citations) <= set(successful)
                and all(
                    any(records[c]["object_id"] == f.object_id for c in citations)
                    for f in plan.facts
                )
            )
            if plan.dependency is not None:
                dep = plan.dependency
                first = [
                    t
                    for identity, t in successful.items()
                    if records[identity]["object_id"] == dep.first_id
                ]
                later = [
                    t
                    for identity, t in successful.items()
                    if records[identity]["object_id"] == dep.next_id
                ]
                matched = matched and any(
                    a["request_index"] < b["request_index"] for a in first for b in later
                )
        except (ValueError, KeyError, TypeError):
            matched = False
        return ResultRecord(
            id="task_outcome",
            version="agent.evaluator.result-record/v1",
            role="diagnostic",
            value=matched,
            value_type="boolean",
            validity="valid",
            failure_reason=None,
            evidence_refs=(ref,),
            unit=None,
            direction=None,
        )


class SkillWorkflowProfile:
    def __init__(self, plan: SkillPlan) -> None:
        self.plan = plan
        self.metrics = MetricPlan(
            (
                MetricBinding(
                    ToolSelection(),
                    "required",
                    json.dumps({"allowed_tools": [plan.tool_name, "read"]}),
                ),
                MetricBinding(
                    SnapshotTask(),
                    "required",
                    canonical_json_bytes(plan.model_dump(mode="json")).decode(),
                ),
                MetricBinding(ToolCalls()),
            )
        )
        self._authority = CaseProfile.model_validate_json(
            canonical_json_bytes(
                {
                    "schema_version": "agent.evaluator.case-profile/v1",
                    "profile_id": "skill-snapshot-workflow",
                    "profile_version": "1.0.0",
                    "description": "Read-only frozen multi-source task assessment",
                    "cases": [
                        {
                            "case_id": plan.case_id,
                            "case_version": "1.0.0",
                            "case_set": "skill-snapshot-workflow",
                            "input": {"prompt": plan.task},
                            "declared_capabilities": ["frozen-read-only"],
                            "fixture_references": [
                                {
                                    "fixture_id": "skill-plan",
                                    "path": "plan.json",
                                    "sha256": plan.sha256,
                                }
                            ],
                        }
                    ],
                    "scorer_policy": {
                        "policy_version": self.metrics.scorer_version,
                        "required_observations": self.metrics.required_observations,
                    },
                    "gate_policy": {
                        "policy_version": "1.0.0",
                        "required_score_ids": ["skill-score"],
                    },
                }
            )
        )

    @property
    def authority(self) -> CaseProfile:
        self.metrics.validate_authority(self._authority)
        return self._authority

    @property
    def projection_version(self) -> str:
        return "cernora.reference.skill-projection/v1"

    def validate_import(self, package: AuthorityBoundImportPackageV2) -> None:
        if package.profile != self.authority or package.case != self.authority.cases[0]:
            raise ContractError("selected Skill Profile authority mismatch")
        bundle = package.content.bundle
        if bundle.terminal.answer is not None:
            wrapper = load_json_bytes(bundle.terminal.answer.content.encode())
            if (
                not isinstance(wrapper, dict)
                or set(wrapper) != {"schema_version", "raw_answer", "capture", "native"}
                or wrapper["schema_version"] != "cernora.reference.skill-answer/v1"
            ):
                raise ContractError("invalid normalized terminal answer")
            native = wrapper["native"]
            if (
                not isinstance(native, dict)
                or set(native)
                != {"plan.json", "events.jsonl", "requests.jsonl", "tools.jsonl", "process.json"}
                or any(not isinstance(raw, str) for raw in native.values())
            ):
                raise ContractError("native capture missing from normalization")
            if SkillPlan.model_validate_json(native["plan.json"]) != self.plan:
                raise ContractError("normalized capture Plan drift")
            verified = audit(self.plan, {name: raw.encode() for name, raw in native.items()})
            if (
                not verified["complete"]
                or wrapper["raw_answer"] != verified["raw_answer"]
                or canonical_json_bytes(wrapper["capture"]) != canonical_json_bytes(verified)
            ):
                raise ContractError("normalized capture disagrees with native evidence")
            if len(bundle.tool_actions) != len(verified["tools"]):
                raise ContractError("normalized tool count mismatch")
            for action, tool in zip(bundle.tool_actions, verified["tools"], strict=True):
                expected_argv = (
                    tool["tool"],
                    *(
                        tool["args"]["argv"]
                        if tool["tool"] == self.plan.tool_name
                        else [tool["args"]["path"]]
                    ),
                )
                if (
                    action.invocation_id != tool["id"]
                    or action.tool != tool["tool"]
                    or action.argv != expected_argv
                    or action.result.exit_code != tool["exit_code"]
                    or not action.result.delivered
                    or not action.result.committed
                    or package.content.artifact_bytes[action.result.stdout_artifact.artifact_id]
                    != tool["stdout"].encode()
                    or package.content.artifact_bytes[action.result.stderr_artifact.artifact_id]
                    != b""
                ):
                    raise ContractError("normalized tool evidence mismatch")

    def assess(
        self, package: AuthorityBoundImportPackageV2, context: ProfileEvaluationContext
    ) -> ProfileAssessment:
        self.validate_import(package)
        b = package.content.bundle
        records = self.metrics.evaluate(MetricContext.from_import(package, context), self.authority)
        f = b.terminal.failure
        evidence = Evidence(
            schema_version="agent.evaluator.evidence/v1",
            evidence_id=context.evidence_id,
            evaluation_id=context.evaluation_id,
            profile_id=package.profile.profile_id,
            case_id=package.case.case_id,
            run_id=b.run.run_id,
            producer=external_producer_identity(
                b.producer.producer_id, b.producer.producer_version
            ),
            process=None,
            answer=None,
            artifacts=tuple(
                Artifact(
                    artifact_id=a.artifact_id, path=a.path, sha256=a.sha256, media_type=a.media_type
                )
                for a in b.artifacts
            ),
            tool_actions=tuple(
                ToolAction(
                    invocation_id=a.invocation_id,
                    tool=a.tool,
                    argv=a.argv,
                    exit_code=a.result.exit_code,
                    timed_out=a.result.status == "timed_out",
                    response_sha256=a.result.stdout_artifact.sha256,
                    committed=a.result.committed,
                    delivered=a.result.delivered,
                )
                for a in b.tool_actions
            ),
            failures=()
            if f is None
            else (
                Failure(domain=f.domain, code=f.code, message=f.message, evidence_references=()),
            ),
            metadata={"projection_version": self.projection_version},
        )
        score = Score(
            schema_version="agent.evaluator.score/v1",
            score_id=context.score_id,
            evidence_id=context.evidence_id,
            scorer_version=self.authority.scorer_policy.policy_version,
            observations=self.metrics.observations(records),
        )
        return ProfileAssessment(evidence, score, self.metrics.required_observations, records)


class SkillCaptureAdapter:
    def __init__(self, plan: SkillPlan) -> None:
        self.plan = plan
        self.profile = SkillWorkflowProfile(plan)

    def adapt(self, completed_export: CompletedExport, output: Path) -> AdaptedBundle:
        plan, native = verify_export(completed_export.root)
        if plan != self.plan:
            raise ContractError("export differs from selected external Plan")
        summary = audit(plan, native)
        files: dict[str, bytes] = {}
        artifacts = []

        def add(name: str, raw: bytes) -> dict[str, str]:
            path = name + ".json"
            files[path] = raw
            artifacts.append(
                {
                    "artifact_id": name,
                    "path": path,
                    "sha256": digest(raw),
                    "size_bytes": len(raw),
                    "media_type": "application/json",
                }
            )
            return {"artifact_id": name, "sha256": digest(raw)}

        actions = []
        previous = None
        for index, tool in enumerate(summary["tools"]):
            action = {
                "sequence": index,
                "invocation_id": tool["id"],
                "tool": tool["tool"],
                "argv": [
                    tool["tool"],
                    *(
                        tool["args"]["argv"]
                        if tool["tool"] == plan.tool_name
                        else [tool["args"]["path"]]
                    ),
                ],
                "result": {
                    "status": "completed" if tool["exit_code"] == 0 else "failed",
                    "exit_code": tool["exit_code"],
                    "committed": True,
                    "delivered": True,
                    "stdout_artifact": add(f"stdout-{index}", tool["stdout"].encode()),
                    "stderr_artifact": add(f"stderr-{index}", b""),
                },
                "previous_receipt_sha256": previous,
            }
            previous = digest(canonical_json_bytes(action))
            action["receipt_sha256"] = previous
            actions.append(action)
        terminal: dict[str, Any] = {
            "status": "inconclusive",
            "answer": None,
            "failure": {
                "domain": "runtime",
                "code": "incomplete-capture",
                "message": "Completed execution evidence unavailable",
            },
        }
        if summary["complete"]:
            # Versioned normalization retains the exact model text and audited capture metadata.
            raw = canonical_json_bytes(
                {
                    "schema_version": "cernora.reference.skill-answer/v1",
                    "raw_answer": summary["raw_answer"],
                    "capture": summary,
                    "native": {name: raw.decode("utf-8") for name, raw in native.items()},
                }
            )
            pointer = add("answer", raw)
            terminal = {
                "status": "completed",
                "answer": {"content": raw.decode(), "sha256": digest(raw), "artifact": pointer},
                "failure": None,
            }
        authority = self.profile.authority
        case = authority.cases[0]
        payload = {
            "schema_version": "agent.evaluator.evidence-bundle/v2",
            "bundle_id": "skill-" + digest(native["events.jsonl"]),
            "producer": {"producer_id": "cernora.reference.pi-skill", "producer_version": "1.0.0"},
            "run": {
                "run_id": "skill-" + digest(native["requests.jsonl"]),
                "attempt_id": "attempt-1",
            },
            "profile": {
                "profile_id": authority.profile_id,
                "profile_version": authority.profile_version,
                "sha256": digest(canonical_json_bytes(authority.model_dump(mode="json"))),
            },
            "case": {
                "case_id": case.case_id,
                "case_version": case.case_version,
                "case_set": case.case_set,
                "sha256": digest(canonical_json_bytes(case.model_dump(mode="json"))),
            },
            "fixtures": [f.model_dump(mode="json") for f in case.fixture_references],
            "tool_actions": actions,
            "artifacts": artifacts,
            "terminal": terminal,
            "infrastructure": {"status": "valid", "failure": None},
        }
        payload["bundle_sha256"] = digest(canonical_json_bytes(payload))
        bundle = EvidenceBundleV2.model_validate_json(canonical_json_bytes(payload))
        files["bundle.json"] = canonical_json_bytes(bundle.model_dump(mode="json"))
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".skill-adapter-", dir=output.parent))
        try:
            for name, raw in files.items():
                (staging / name).write_bytes(raw)
            atomic_publish_directory(staging, output)
        finally:
            if staging.exists():
                import shutil

                shutil.rmtree(staging)
        return AdaptedBundle(bundle_path=output / "bundle.json")


def evaluate_export(plan: SkillPlan, source: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ContractError("evaluation output must be new")
    adapter = SkillCaptureAdapter(plan)
    bundle = adapter.adapt(CompletedExport(source), output / "bundle")
    import_evidence_bundle_v2(
        profile=adapter.profile, bundle_path=bundle.bundle_path, output=output / "import"
    )
    receipt = evaluate_imported_case(adapter.profile, output / "import", output / "evaluation")
    if read_imported_evaluation(output / "evaluation", adapter.profile) != receipt:
        raise ContractError("Skill evaluation strict reload mismatch")
    report = read_evaluation_report(output / "evaluation", adapter.profile)
    if report is None or report.conclusion != receipt.case_outcome:
        raise ContractError("Skill report strict reload mismatch")
    _, native = verify_export(source)
    summary = audit(plan, native)
    result = {
        k: summary[k]
        for k in (
            "skill_loading",
            "explicit_loading_observed",
            "implicit_loading_observed",
            "resources",
            "requests",
            "usage_complete",
            "total_tokens",
            "observed_tokens",
            "cost_scope",
            "cost_currency",
            "wall_ms",
            "stop_reason",
        )
    }
    result.update(
        {"outcome": receipt.case_outcome, "plan_sha256": plan.sha256, "strict_reload": True}
    )
    (output / "summary.json").write_bytes(canonical_json_bytes(result))
    return result
