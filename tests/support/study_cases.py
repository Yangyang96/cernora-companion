"""Deterministic case, plan and comparison fixtures for retained Study tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from cernora_reference_workflow.common import (
    canonical_content_id,
    canonical_json_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    materialize_comparison_plan,
    materialize_treatment_declaration,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    materialize_controlled_experiment_spec,
    materialize_dataset_authority,
)
from cernora_reference_workflow.controlled_profile import (
    PROFILE_ID,
    PROFILE_VERSION,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
    reconstructed_revealed_case,
    task_from_revealed_case,
)
from cernora_reference_workflow.heldout_seal import (
    HeldoutArchiveCase,
    HeldoutManifest,
)
from tests.unit.test_controlled_run_plan import valid_m4_payload

VISIBLE_ROOT = Path("examples/m4-visible")

DEV_NAMES = ("dev-integer-ledger", "dev-interval-merge", "dev-query-codec")

REG_NAMES = ("reg-csv-rollup", "reg-graph-topology", "reg-posix-normalization")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _tasks(names: tuple[str, ...]) -> tuple[ControlledTaskAuthority, ...]:
    return tuple(load_visible_task(VISIBLE_ROOT / item) for item in names)


def _manifest() -> HeldoutManifest:
    source = HeldoutManifest.from_file(Path("examples/m4-heldout-sealed/manifest.json"))
    heldout = tuple(task for task in _all_task_authorities(source) if task.split_id == "held-out")
    cases = [_revealed_case_authority(task) for task in heldout]
    commitments = [
        {
            "case_id": task.case.case_id,
            "plaintext_sha256": sha256_bytes(canonical_json_bytes(case)),
        }
        for task, case in zip(heldout, cases, strict=True)
    ]
    suite = {
        "schema_version": "cernora.reference.heldout-suite-commitment/v1",
        "cases": commitments,
    }
    archive = {
        "schema_version": "cernora.reference.heldout-archive/v1",
        "cases": cases,
    }
    payload = source.model_dump(mode="json", exclude={"manifest_id"})
    payload["case_commitments"] = commitments
    payload["suite_sha256"] = sha256_bytes(canonical_json_bytes(suite))
    payload["archive_sha256"] = sha256_bytes(canonical_json_bytes(archive))
    aad = {
        "schema_version": "cernora.reference.heldout-seal-aad/v1",
        "algorithm": payload["algorithm"],
        "case_commitments": commitments,
        "suite_sha256": payload["suite_sha256"],
        "archive_sha256": payload["archive_sha256"],
        "nonce": payload["nonce"],
    }
    payload["aad_sha256"] = sha256_bytes(canonical_json_bytes(aad))
    payload["manifest_id"] = canonical_content_id(payload, excluded=frozenset())
    return HeldoutManifest.model_validate(payload)


def _generic_heldout_case(case_id: str, template: ControlledTaskAuthority) -> HeldoutArchiveCase:
    files = (*template.workspace_files, *template.test_files)
    return HeldoutArchiveCase(
        case_id=case_id,
        task={
            "schema_version": "cernora.reference.heldout-task/v1",
            "language": "python",
            "instruction": template.case.input.prompt,
            "allowed_paths": list(template.allowed_paths),
            "protected_paths": list(template.protected_paths),
            "case_version": template.case.case_version,
        },
        workspace={
            "schema_version": "cernora.reference.heldout-workspace/v1",
            "files": [
                {"path": item.path, "content_utf8": item.content().decode("utf-8")}
                for item in files
            ],
        },
        evaluation={
            "schema_version": "cernora.reference.heldout-evaluation/v1",
            "command": list(template.test_command),
            "working_directory": ".",
            "timeout_seconds": 60,
            "network": "disabled",
            "expected_exit_code": 0,
            "success_metric": "verifier_exit_zero",
            "failure_codes": [template.failure_code],
        },
    )


def _revealed_case_authority(task: ControlledTaskAuthority) -> dict[str, object]:
    return reconstructed_revealed_case(task).model_dump(mode="json")


def _all_task_authorities(manifest: HeldoutManifest) -> tuple[ControlledTaskAuthority, ...]:
    visible = _tasks((*DEV_NAMES, *REG_NAMES))
    template = visible[0]
    heldout = tuple(
        task_from_revealed_case(_generic_heldout_case(commitment.case_id, template))
        for commitment in manifest.case_commitments
    )
    return (*visible, *heldout)


def _final_plan(
    manifest: HeldoutManifest,
    *,
    candidate_prompt: str = "Inspect the leading failure, then repair the project.",
    task_authorities: tuple[ControlledTaskAuthority, ...] | None = None,
) -> ControlledRunPlanV2:
    from tests.unit.test_controlled_live_attempt import _spec

    tasks = tuple(
        sorted(
            _all_task_authorities(manifest) if task_authorities is None else task_authorities,
            key=lambda item: item.case.case_id,
        )
    )
    failure_binding = {
        "code": "interval_boundary_v1",
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
    }
    raw_specs = tuple(
        _spec(
            task,
            configuration_id=configuration_id,
            prompt=prompt,
            candidate_failure_binding=(
                failure_binding if configuration_id == "candidate" else None
            ),
            profile_tasks=tasks,
        )
        for task in tasks
        for configuration_id, prompt in (
            ("baseline", "Repair the project."),
            ("candidate", candidate_prompt),
        )
    )
    dataset = materialize_dataset_authority(
        tuple(item.dataset_authority.cases[0] for item in raw_specs[::2])
    )
    specs = []
    for item in raw_specs:
        payload = item.model_dump(mode="json", exclude={"experiment_id"})
        payload["dataset_authority"] = dataset.model_dump(mode="json")
        specs.append(materialize_controlled_experiment_spec(payload))
    plan_payload = valid_m4_payload(case_ids=tuple(task.case.case_id for task in tasks))
    plan_payload["experiment_specs"] = [item.model_dump(mode="json") for item in specs]
    plan_payload["cases"] = [
        {
            "case_id": item.task.task_id,
            "case_version": item.task.task_version,
            "task_content_sha256": item.task.content_sha256,
        }
        for item in specs[::2]
    ]
    plan_payload["cells"] = [
        {
            "case_id": item.task.task_id,
            "configuration_id": item.configuration_id,
            "experiment_id": item.experiment_id,
        }
        for item in specs
    ]
    return materialize_controlled_run_plan(plan_payload)


def _comparison(plan: ControlledRunPlanV2, manifest: HeldoutManifest) -> ComparisonPlanV1:
    split_by_case = {
        **{item: "development" for item in DEV_NAMES},
        **{item: "regression" for item in REG_NAMES},
        **{item.case_id: "held-out" for item in manifest.case_commitments},
    }
    statistics = plan.experiment_specs[0].statistical_policy
    return materialize_comparison_plan(
        {
            "schema_version": "cernora.reference.comparison-plan/v1",
            "source_run_plan_id": plan.run_plan_id,
            "baseline_configuration_id": "baseline",
            "candidate_configuration_id": "candidate",
            "case_splits": [
                {"case_id": item.case_id, "split_id": split_by_case[item.case_id]}
                for item in plan.cases
            ],
            "treatment": materialize_treatment_declaration(("prompt_instruction",)).model_dump(
                mode="json"
            ),
            "primary_outcome": {
                "metric": "reliable_success_rate",
                "scope": "split",
                "split_id": "held-out",
                "direction": "higher_is_better",
                "practical_threshold_basis_points": 1000,
            },
            "guardrails": [
                {
                    "guardrail_id": "evaluation-validity",
                    "hard": True,
                    "metric": "evaluation_validity_rate",
                    "scope": "all",
                    "split_id": None,
                    "direction": "higher_is_better",
                    "max_adverse_basis_points": 0,
                    "profile_id": None,
                    "profile_version": None,
                    "failure_code": None,
                },
                {
                    "guardrail_id": "protected-paths",
                    "hard": True,
                    "metric": "profile_failure_code_rate",
                    "scope": "all",
                    "split_id": None,
                    "direction": "lower_is_better",
                    "max_adverse_basis_points": 0,
                    "profile_id": PROFILE_ID,
                    "profile_version": PROFILE_VERSION,
                    "failure_code": "protected_paths_unchanged_v1",
                },
                {
                    "guardrail_id": "regression-rsr",
                    "hard": True,
                    "metric": "reliable_success_rate",
                    "scope": "split",
                    "split_id": "regression",
                    "direction": "higher_is_better",
                    "max_adverse_basis_points": 1000,
                    "profile_id": None,
                    "profile_version": None,
                    "failure_code": None,
                },
            ],
            "bootstrap": statistics.bootstrap.model_dump(mode="json"),
            "pass_k": (
                None if statistics.pass_k is None else statistics.pass_k.model_dump(mode="json")
            ),
            "statistical_policy": statistics.model_dump(mode="json"),
        }
    )
