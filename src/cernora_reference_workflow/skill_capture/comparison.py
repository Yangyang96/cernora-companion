"""Frozen two-arm Skill comparison; Core owns all aggregation and conclusions."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal, Self

from cernora import (
    CaseProfile,
    ComparisonGuardrail,
    MetricBinding,
    MetricContext,
    MetricDefinition,
    MetricPlan,
    PrimaryOutcome,
    ResultRecord,
    ToolCalls,
    ToolSelection,
    TreatmentChange,
    component_identity,
    embed_evaluation_package,
    materialize_batch_input,
    materialize_comparison_input,
    materialize_experiment_authority,
    materialize_treatment,
    reload_batch_summary_package,
    reload_comparison_package,
    summarize_batch,
    summarize_comparison,
)
from pydantic import Field, model_validator

from cernora_reference_workflow.common import ContractError, canonical_json_bytes, load_json_file
from cernora_reference_workflow.controlled_experiment_spec import (
    materialize_expected_evaluation_authority,
)
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.skill_capture.audit import audit
from cernora_reference_workflow.skill_capture.contracts import (
    SkillContract,
    SkillPlan,
    digest,
    verify_export,
)
from cernora_reference_workflow.skill_capture.diagnostics import diagnose_export
from cernora_reference_workflow.skill_capture.evaluation import SkillWorkflowProfile, SnapshotTask


def identity(value: Any) -> str:
    return digest(canonical_json_bytes(value))


def task_contract(plan: SkillPlan) -> dict[str, Any]:
    data = plan.model_dump(mode="json")
    return {k: data[k] for k in ("case_id", "task", "tool_name", "objects", "facts", "dependency")}


class SkillComparisonPlan(SkillContract):
    schema_version: Literal["cernora.reference.skill-comparison-plan/v1"]
    baseline: SkillPlan
    candidate: SkillPlan
    repetitions: int = Field(ge=1, le=100)
    purpose: Literal["synthetic_validation", "prospective_experiment"]
    practical_threshold_basis_points: int = Field(ge=0, le=10000)

    @model_validator(mode="after")
    def same_task(self) -> Self:
        if task_contract(self.baseline) != task_contract(self.candidate):
            raise ValueError("comparison must preserve the same task, sources, and reference")
        if self.baseline.sha256 == self.candidate.sha256:
            raise ValueError("comparison requires a declared configuration change")
        return self

    @property
    def sha256(self) -> str:
        return identity(self.model_dump(mode="json"))

    @classmethod
    def read(cls, path: Path) -> SkillComparisonPlan:
        return cls.model_validate_json(canonical_json_bytes(load_json_file(path)))


class StudyCase(SkillContract):
    split: Literal["development", "workflow_check"]
    baseline: SkillPlan
    candidate: SkillPlan


class SkillStudyPlan(SkillContract):
    schema_version: Literal["cernora.reference.skill-comparison-plan/v2"]
    cases: tuple[StudyCase, ...] = Field(min_length=2)
    repetitions: int = Field(ge=1, le=100)
    purpose: Literal["synthetic_validation", "prospective_experiment"]
    practical_threshold_basis_points: int = Field(ge=0, le=10000)
    primary_split: Literal["workflow_check"]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        ids = [c.baseline.case_id for c in self.cases]
        if ids != sorted(set(ids)):
            raise ValueError("Study Cases must be sorted and unique")
        if {c.split for c in self.cases} != {"development", "workflow_check"}:
            raise ValueError("Study needs development and workflow_check groups")
        seen: set[str] = set()
        for c in self.cases:
            if task_contract(c.baseline) != task_contract(c.candidate):
                raise ValueError("Study arms must preserve task and references")
            objects = {o.object_id for o in c.baseline.objects}
            if seen & objects:
                raise ValueError("related objects cannot cross Case groups")
            seen.update(objects)
        for arm in ("baseline", "candidate"):
            global_values = []
            for c in self.cases:
                data = getattr(c, arm).model_dump(mode="json")
                global_values.append(
                    {
                        k: v
                        for k, v in data.items()
                        if k not in {"case_id", "task", "objects", "facts", "dependency"}
                    }
                )
            if any(v != global_values[0] for v in global_values):
                raise ValueError("configuration-global settings drift across Cases")
        return self

    @property
    def sha256(self) -> str:
        return identity(self.model_dump(mode="json"))


Plan = SkillComparisonPlan | SkillStudyPlan


def pairs(plan: Plan) -> list[tuple[str, SkillPlan, SkillPlan]]:
    if isinstance(plan, SkillStudyPlan):
        return [(c.split, c.baseline, c.candidate) for c in plan.cases]
    return [("development", plan.baseline, plan.candidate)]


def parse_plan(value: Any) -> Plan:
    cls = (
        SkillStudyPlan
        if value.get("schema_version") == "cernora.reference.skill-comparison-plan/v2"
        else SkillComparisonPlan
    )
    return cls.model_validate_json(canonical_json_bytes(value))


class StudyTask(SnapshotTask):
    definition = MetricDefinition(
        metric_id="task_outcome", metric_version="2.0.0", value_type="boolean"
    )

    def validate_parameters(self, parameters_json: str) -> None:
        SkillStudyPlan.model_validate_json(parameters_json)

    def evaluate(self, context: MetricContext, parameters_json: str) -> ResultRecord:
        study = SkillStudyPlan.model_validate_json(parameters_json)
        selected = study.cases[0].baseline
        if context.terminal_artifact is not None:
            raw, _ = context.artifact(context.terminal_artifact)
            wrapper = json.loads(raw)
            actual = SkillPlan.model_validate_json(wrapper["native"]["plan.json"])
            matched = [c.baseline for c in study.cases if actual in (c.baseline, c.candidate)]
            if len(matched) != 1:
                raise ContractError("terminal capture outside frozen Study")
            selected = matched[0]
        result = super().evaluate(
            context, canonical_json_bytes(selected.model_dump(mode="json")).decode()
        )
        return result


class ComparisonProfile(SkillWorkflowProfile):
    """A versioned shared scoring authority, bound to both predeclared captures.

    V1 scoring remains unchanged. Reference facts are equal in both plans; a
    shared roster fixes the authority before any evidence is inspected.
    """

    def __init__(self, comparison: Plan, selected: SkillPlan) -> None:
        roster = pairs(comparison)
        if selected not in [p for _, left, right in roster for p in (left, right)]:
            raise ContractError("capture not in declared comparison")
        super().__init__(roster[0][1])
        self.plan = selected
        self.study = isinstance(comparison, SkillStudyPlan)
        if self.study:
            self.metrics = MetricPlan(
                (
                    MetricBinding(
                        ToolSelection(),
                        "required",
                        json.dumps({"allowed_tools": [selected.tool_name, "read"]}),
                    ),
                    MetricBinding(
                        StudyTask(),
                        "required",
                        canonical_json_bytes(comparison.model_dump(mode="json")).decode(),
                    ),
                    MetricBinding(ToolCalls()),
                )
            )
        payload = self._authority.model_dump(mode="json")
        payload["profile_version"] = "3.0.0" if self.study else "2.0.0"
        payload["scorer_policy"]["policy_version"] = self.metrics.scorer_version
        payload["cases"] = []
        for _, baseline, _ in roster:
            case = SkillWorkflowProfile(baseline).authority.cases[0].model_dump(mode="json")
            case["fixture_references"] = [
                dict(
                    fixture_id="comparison-plan",
                    path="comparison-plan.json",
                    sha256=comparison.sha256,
                )
            ]
            payload["cases"].append(case)
        self._authority = CaseProfile.model_validate_json(canonical_json_bytes(payload))

    @property
    def projection_version(self) -> str:
        return (
            "cernora.reference.skill-projection/v3"
            if self.study
            else "cernora.reference.skill-projection/v2"
        )


def expected_authority(profile: SkillWorkflowProfile) -> dict[str, Any]:
    p = profile.authority
    case = next(c for c in p.cases if c.case_id == profile.plan.case_id)
    projection = dict(name="imported_projection", version=profile.projection_version)
    value = materialize_expected_evaluation_authority(
        dict(
            schema_version="agent.evaluator.imported-evaluation-authority/v1",
            profile=dict(
                profile_id=p.profile_id,
                profile_version=p.profile_version,
                sha256=identity(p.model_dump(mode="json")),
            ),
            case=dict(
                case_id=case.case_id,
                case_version=case.case_version,
                case_set=case.case_set,
                sha256=identity(case.model_dump(mode="json")),
            ),
            fixtures=[f.model_dump(mode="json") for f in case.fixture_references],
            projection={**projection, "sha256": identity(projection), "digest_kind": "identity"},
            scorer=component_identity("scorer", p.scorer_policy.policy_version).model_dump(
                mode="json"
            ),
            case_gate=component_identity("gate_policy", p.gate_policy.policy_version).model_dump(
                mode="json"
            ),
        )
    )
    return value.model_dump(mode="json")


def policies(plan: Plan) -> dict[str, Any]:
    result: dict[str, Any] = dict(
        primary_outcome=PrimaryOutcome(
            metric="reliable_success_rate",
            scope="split" if isinstance(plan, SkillStudyPlan) else "all",
            split_id=plan.primary_split if isinstance(plan, SkillStudyPlan) else None,
            direction="higher_is_better",
            practical_threshold_basis_points=plan.practical_threshold_basis_points,
        ).model_dump(mode="json"),
        guardrails=[
            ComparisonGuardrail(
                guardrail_id="validity",
                hard=True,
                metric="evaluation_validity_rate",
                scope="all",
                direction="higher_is_better",
                max_adverse_basis_points=0,
            ).model_dump(mode="json")
        ]
        + (
            [
                ComparisonGuardrail(
                    guardrail_id="development-success",
                    hard=True,
                    metric="reliable_success_rate",
                    scope="split",
                    split_id="development",
                    direction="higher_is_better",
                    max_adverse_basis_points=0,
                ).model_dump(mode="json")
            ]
            if isinstance(plan, SkillStudyPlan)
            else []
        ),
        bootstrap=dict(
            method="case-clustered-paired-bootstrap/v1",
            confidence_basis_points=9500,
            resamples=10000,
            percentile="nearest_rank_closed",
            seed_source="comparison_input_sha256",
        ),
        pass_k=None,
    )

    result["guardrails"].sort(key=lambda item: item["guardrail_id"])
    return result


def authorities(plan: Plan) -> list[dict[str, Any]]:
    result = []
    for arm, capture in [
        (arm, p)
        for _, left, right in pairs(plan)
        for arm, p in (("baseline", left), ("candidate", right))
    ]:
        profile = ComparisonProfile(plan, capture)
        expected = expected_authority(profile)
        policy = dict(
            schema_version="agent.evaluator.comparison-evaluation-policy/v1",
            **{k: expected[k] for k in ("profile", "projection", "scorer", "case_gate")},
        )
        data = capture.model_dump(mode="json")

        def select(names: tuple[str, ...], values: dict[str, Any] = data) -> str:
            return identity({k: values[k] for k in names})

        projection = dict(
            prompt_instruction_sha256=select(
                ("system", "skill_name", "skill_version", "skill_files", "invocation")
            ),
            runtime_version_sha256=select(("runtime_version", "extension_sha256")),
            model_sha256=select(("provider", "model", "base_url")),
            tool_schema_sha256=select(
                ("tool_name", "command_path", "id_option", "extension_sha256")
            ),
            generation_configuration_sha256=select(("thinking", "max_output_tokens")),
            timeout_sha256=select(("timeout_seconds",)),
            resources_sha256=select(("max_requests", "max_tool_calls", "max_request_bytes")),
            retry_policy_sha256=select(("retries",)),
            dataset_sha256=identity([task_contract(left) for _, left, _ in pairs(plan)])
            if isinstance(plan, SkillStudyPlan)
            else identity(task_contract(capture)),
            profile_sha256=expected["profile"]["sha256"],
            evaluation_authority_sha256=expected["authority_sha256"],
            evaluation_policy_sha256=identity(policy),
            report_contract_sha256=identity("cernora.reference.skill-diagnostic/v1"),
            statistical_plan_sha256=identity(policies(plan)),
        )
        result.append(
            materialize_experiment_authority(
                dict(
                    schema_version="agent.evaluator.comparison-experiment-authority/v1",
                    configuration_id=arm,
                    case=expected["case"],
                    projection=projection,
                )
            ).model_dump(mode="json")
        )
    return result


def freeze_comparison(plan: Plan) -> dict[str, Any]:
    authority = authorities(plan)
    changes = []
    for kind in (
        "prompt_instruction",
        "model",
        "tool_schema",
        "generation_configuration",
        "runtime_version",
    ):
        key = kind + "_sha256"
        a, b = (v["projection"][key] for v in authority[:2])
        if a != b:
            changes.append(TreatmentChange(kind=kind, baseline_sha256=a, candidate_sha256=b))
    if not changes:
        raise ContractError(
            "no supported Treatment difference; change only budgets is not a treatment"
        )
    treatment = materialize_treatment(changes)
    slots: list[dict[str, Any]] = []
    for repetition in range(1, plan.repetitions + 1):
        ordered = authority
        if isinstance(plan, SkillStudyPlan):
            ordered = []
            for index in range(0, len(authority), 2):
                pair = authority[index : index + 2]
                ordered.extend(pair if (index // 2 + repetition) % 2 else reversed(pair))
        for a in ordered:
            coordinate = dict(
                case_id=a["case"]["case_id"],
                configuration_id=a["configuration_id"],
                experiment_id=a["experiment_id"],
                repetition=repetition,
            )
            slots.append(
                dict(
                    slot_index=len(slots) + 1,
                    trial_slot_id=identity(dict(plan=plan.sha256, **coordinate)),
                    **coordinate,
                )
            )
    return dict(
        schema_version="cernora.reference.skill-comparison-freeze/v1",
        plan=plan.model_dump(mode="json"),
        plan_sha256=plan.sha256,
        experiment_authorities=authority,
        treatment=treatment.model_dump(mode="json"),
        slots=slots,
        **policies(plan),
    )


def _compare_exports(freeze_path: Path, sources_path: Path, output: Path) -> dict[str, Any]:
    frozen = load_json_file(freeze_path)
    plan = parse_plan(frozen["plan"])
    if canonical_json_bytes(frozen) != canonical_json_bytes(freeze_comparison(plan)):
        raise ContractError("comparison freeze authority mismatch")
    sources = load_json_file(sources_path)
    if not isinstance(sources, dict) or set(sources) != {
        s["trial_slot_id"] for s in frozen["slots"]
    }:
        raise ContractError("sources must cover exactly every frozen slot")
    if any(not isinstance(p, str) for p in sources.values()):
        raise ContractError("source locations must be paths")
    if output.exists():
        raise ContractError("comparison output must be new")
    # Reject missing, reused, or mismatched native evidence before creating output.
    verified = {}
    seen = set()
    for slot in frozen["slots"]:
        key = slot["trial_slot_id"]
        root = Path(sources[key])
        roster = next(
            (left, right) for _, left, right in pairs(plan) if left.case_id == slot["case_id"]
        )
        selected = roster[0] if slot["configuration_id"] == "baseline" else roster[1]
        captured, files = verify_export(root)
        if captured != selected:
            raise ContractError("slot capture Plan mismatch")
        manifest = identity({k: digest(v) for k, v in files.items()})
        attempt_fingerprint = identity(
            [digest(files["events.jsonl"]), digest(files["requests.jsonl"])]
        )
        if attempt_fingerprint in seen:
            raise ContractError("same captured attempt cannot fill multiple Trial slots")
        seen.add(attempt_fingerprint)
        verified[key] = (root, selected, files, audit(selected, files), manifest)
    output.mkdir(parents=True)
    execution_id = identity(dict(plan=plan.sha256, exports=sorted(seen)))
    trials = []
    diagnostics = []
    for slot in frozen["slots"]:
        key = slot["trial_slot_id"]
        root, selected, files, observed, manifest = verified[key]
        profile = ComparisonProfile(plan, selected)
        folder = output / "trials" / key
        diagnostic = diagnose_export(selected, root, folder, profile=profile)
        receipt = diagnostic["evaluation_receipt"]
        if receipt["authority"] != expected_authority(profile):
            raise ContractError("evaluation differs from frozen expected authority")
        if verify_export(root)[1] != files:
            raise ContractError("source changed during comparison")
        evaluation = embed_evaluation_package(folder / "evaluation")
        trial_id = identity(dict(execution=execution_id, slot=key))
        attempt_id = identity(dict(trial=trial_id, manifest=manifest))
        trials.append(
            dict(
                schema_version="agent.evaluator.batch-trial/v1",
                run_plan_id=plan.sha256,
                execution_id=execution_id,
                trial_id=trial_id,
                **slot,
                selected_attempt_id=attempt_id,
                attempts=[
                    dict(
                        schema_version="agent.evaluator.batch-attempt/v1",
                        attempt_id=attempt_id,
                        source_attempt_id=receipt["run"]["attempt_id"],
                        trial_id=trial_id,
                        ordinal=1,
                        predecessor_attempt_id=None,
                        source_manifest_sha256=manifest,
                        retry_eligible=False,
                        resources=dict(
                            duration_milliseconds=observed["wall_ms"],
                            input_tokens=None,
                            output_tokens=None,
                            cost_microunits=None,
                            usage_receipt_sha256=None,
                        ),
                        evaluation=evaluation.model_dump(mode="json"),
                        lifecycle=None,
                    )
                ],
            )
        )
        diagnostics.append(
            dict(
                slot=slot,
                task=diagnostic["summary"],
                findings=diagnostic["findings"],
                report=f"trials/{key}/diagnostics.md",
                observed_tokens=observed["observed_tokens"],
                total_tokens=observed["total_tokens"],
            )
        )
    batch = materialize_batch_input(
        dict(
            schema_version="agent.evaluator.batch-input/v1",
            run_plan_id=plan.sha256,
            execution_id=execution_id,
            execution_status="completed",
            budget_status="within_budget",
            companion_version="0.3.0",
            planned_trial_count=len(trials),
            attempt_count=len(trials),
            planned_trials=frozen["slots"],
            trials=trials,
        )
    )
    summarize_batch(batch, output / "batch")
    batch_package = reload_batch_summary_package(output / "batch")
    experiment_by_key = {
        (a["case"]["case_id"], a["configuration_id"]): a for a in frozen["experiment_authorities"]
    }
    comparison_cases = []
    for split, left, _ in pairs(plan):
        a = experiment_by_key[(left.case_id, "baseline")]
        b = experiment_by_key[(left.case_id, "candidate")]
        comparison_cases.append(
            dict(
                case=a["case"],
                split_id=split,
                baseline_experiment_id=a["experiment_id"],
                candidate_experiment_id=b["experiment_id"],
            )
        )
    comparison = materialize_comparison_input(
        dict(
            schema_version="agent.evaluator.comparison-input/v1",
            batch_input=batch_package.batch_input.model_dump(mode="json"),
            baseline=dict(configuration_id="baseline"),
            candidate=dict(configuration_id="candidate"),
            experiment_authorities=frozen["experiment_authorities"],
            cases=comparison_cases,
            treatment=frozen["treatment"],
            **policies(plan),
        )
    )
    summarize_comparison(comparison, output / "comparison")
    package = reload_comparison_package(output / "comparison")
    report = dict(
        schema_version="cernora.reference.skill-comparison-report/v1",
        purpose=plan.purpose,
        conclusion=package.summary.conclusion,
        comparison_summary=package.summary.model_dump(mode="json"),
        batch_summary=batch_package.summary.model_dump(mode="json"),
        trials=diagnostics,
        limitations=[
            "Configured Case groups only; no independent real-world holdout or population claim.",
            "Freeze before collection; old smoke is not controlled evidence.",
            "Runtime tokens are diagnostic; token breakdown and billing remain unavailable.",
            "Synthetic validation establishes software behavior, not model improvement.",
        ],
    )
    (output / "report.json").write_bytes(canonical_json_bytes(report))
    (output / "freeze.json").write_bytes(canonical_json_bytes(frozen))
    lines = [
        "# Skill comparison",
        "",
        f"Core conclusion: **{package.summary.conclusion}**. Purpose: {plan.purpose}.",
        "",
        "[Comparison](comparison/comparison-summary.md) · [Batch](batch/batch-summary.md)",
        "",
        "| Arm | Task | Skill loading | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {d['slot']['configuration_id']} | {d['task']['outcome']} | "
        f"{d['task']['skill_loading']} | [diagnostics]({d['report']}) |"
        for d in diagnostics
    )
    lines.extend(["", *report["limitations"], ""])
    (output / "report.md").write_text("\n".join(lines))
    return report


def compare_exports(freeze_path: Path, sources_path: Path, output: Path) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise ContractError("comparison output must be new")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".skill-comparison-", dir=output.parent))
    staging.rmdir()
    try:
        result = _compare_exports(freeze_path, sources_path, staging)
        atomic_publish_directory(staging, output)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)
