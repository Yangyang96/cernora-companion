#!/usr/bin/env python3
"""Materialize the twelve-Case P4 Controlled Study up to the reveal boundary.

Assembles the post-reveal study authorities — the twelve task set (nine
visible Cases plus three revealed held-out Cases), the 72-Trial
ControlledRunPlanV2, the split-scoped ComparisonPlanV1, the re-minted
Candidate Development record (continuity-gated against the frozen worksheet
record), the HeldoutCommitment over the public seal manifest, and the
ImplementationLock — then verifies everything through the strict study
bindings and optionally runs the offline custody ceremony (prepare,
request-reveal, bind-reveal), stopping before start-execution.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from cernora import BootstrapPlan, PassKPlan

from cernora_reference_workflow.candidate_development import (
    CandidateDevelopmentRecord,
    candidate_continuity_violations,
    freeze_candidate_development,
)
from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
    sha256_file,
    validate_sha256,
)
from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    materialize_treatment_declaration,
)
from cernora_reference_workflow.comparison_plan import (
    materialize_comparison_plan as materialize_comparison_plan_contract,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    ACCEPTED_CORE_0_1_4_WHEEL_SHA256,
    CanonicalAuthoritySource,
    materialize_authority_source,
    materialize_statistical_policy,
)
from cernora_reference_workflow.controlled_profile import PROFILE_ID, PROFILE_VERSION
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.controlled_spec_builder import (
    build_controlled_specifications,
)
from cernora_reference_workflow.controlled_study import (
    StudyIntent,
    StudyProtocol,
    compile_study_protocol,
    materialize_heldout_commitment,
    materialize_heldout_reveal,
    materialize_implementation_lock,
    materialize_study_analysis_policy,
    materialize_study_intent,
)
from cernora_reference_workflow.controlled_study import (
    advance as advance_study,
)
from cernora_reference_workflow.controlled_study import (
    prepare as prepare_study,
)
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
)
from cernora_reference_workflow.heldout_seal import (
    HeldoutManifest,
    HeldoutSealError,
)
from cernora_reference_workflow.runtime_policy import (
    PI_VERSION,
    RUNTIME_CONFIGURATION_SHA256,
)
from cernora_reference_workflow.spec_builder import BASE_IMAGE
from cernora_reference_workflow.study_projection import (
    StudyRunPlanBinding,
    bind_study_run_plan,
    case_authority_sha256,
    configuration_authority_sha256,
)

STUDY_KIND = "confirmatory-effect"
REPETITIONS = 3
PLANNED_TRIAL_COUNT = 72
MAX_ATTEMPT_COUNT = 144
MAX_WALL_SECONDS = 160_000
AGENT_TIMEOUT_SECONDS = 1_800
BASELINE_PROMPT_SOURCE_ID = "p4-confirmatory-baseline-prompt-v1"
CANDIDATE_PROMPT_SOURCE_ID = "p4-confirmatory-candidate-prompt-v1"
BASELINE_PROMPT_SHA256 = "acf0b631678cdc9b3c87e7c2d845b3cc28f84b316cf1e2de6d6954adfb856eb5"
BASELINE_PROMPT_TEXT = (
    "Repair the task from its declared behavior and the available workspace evidence."
)
ACCEPTED_COMPANION_VERSION = "0.4.2"
HARNESS_VERSION = "0.16.1"
REVEAL_POLICY_PAYLOAD: Mapping[str, object] = {
    "schema_version": "cernora.reference.heldout-reveal-policy/v1",
    "boundary": "user-authorized-one-shot",
    "key_custody_path": "~/.cernora/p4-heldout/reveal.key",
    "receipt_binding": "frozen-candidate-development-record",
    "commitment": "preparations/p4-heldout-commitment/manifest.json",
}
FileIdentity = tuple[int, int]


@dataclasses.dataclass(frozen=True)
class P4StudyAssembly:
    """Verified study authorities with their fail-closed bindings complete."""

    intent: StudyIntent
    protocol: StudyProtocol
    run_plan: ControlledRunPlanV2
    comparison_plan: ComparisonPlanV1
    development: CandidateDevelopmentRecord
    binding: StudyRunPlanBinding


def _exclusive_write(path: Path, payload: bytes, *, mode: int) -> FileIdentity:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    opened = os.fstat(descriptor)
    identity = (opened.st_dev, opened.st_ino)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    finally:
        with contextlib.suppress(OSError):
            os.close(descriptor)
    return identity


def _load_json_object(path: Path) -> dict[str, object]:
    payload = load_json_bytes(read_regular_file_bytes(path))
    if not isinstance(payload, dict):
        raise ContractError("study input must be a JSON object")
    return payload


def _load_canonical_json(path: Path) -> dict[str, object]:
    data = read_regular_file_bytes(path)
    payload = _load_json_object(path)
    if canonical_json_bytes(payload) != data:
        raise ContractError("study input is not canonical JSON")
    return payload


def _load_heldout_manifest(path: Path) -> HeldoutManifest:
    data = read_regular_file_bytes(path)
    manifest = HeldoutManifest.from_file(path)
    if manifest.canonical_bytes() != data:
        raise ContractError("held-out manifest is not canonical JSON")
    return manifest


def _load_prior_record(path: Path) -> CandidateDevelopmentRecord:
    data = read_regular_file_bytes(path)
    payload = _load_json_object(path)
    record = CandidateDevelopmentRecord.model_validate(payload)
    if canonical_json_bytes(record.model_dump(mode="json")) != data:
        raise ContractError("Candidate Development record is not canonical JSON")
    return record


def _load_candidate_prompt(path: Path) -> CanonicalAuthoritySource:
    data = read_regular_file_bytes(path)
    payload = _load_json_object(path)
    source = CanonicalAuthoritySource.model_validate(payload)
    if canonical_json_bytes(source.model_dump(mode="json")) != data:
        raise ContractError("candidate prompt authority is not canonical JSON")
    return source


def _load_visible_tasks(visible_root: Path) -> tuple[ControlledTaskAuthority, ...]:
    if not visible_root.is_dir() or visible_root.is_symlink():
        raise ContractError("visible corpus root must be one real directory")
    roots = tuple(
        sorted(path for path in visible_root.iterdir() if path.is_dir() and not path.is_symlink())
    )
    tasks = tuple(load_visible_task(path) for path in roots)
    split_counts = {
        split_id: sum(item.split_id == split_id for item in tasks)
        for split_id in ("development", "regression")
    }
    if (
        len(tasks) != 9
        or len({item.case.case_id for item in tasks}) != 9
        or split_counts != {"development": 6, "regression": 3}
    ):
        raise ContractError(
            "visible corpus must contain exact six/three development/regression cases"
        )
    return tasks


def _load_heldout_tasks(paths: tuple[Path, ...]) -> tuple[ControlledTaskAuthority, ...]:
    tasks = tuple(
        ControlledTaskAuthority.from_bytes(read_regular_file_bytes(path)) for path in paths
    )
    case_ids = tuple(task.case.case_id for task in tasks)
    if (
        len(tasks) != 3
        or len(case_ids) != len(set(case_ids))
        or any(task.split_id != "held-out" for task in tasks)
    ):
        raise ContractError("revealed task root must contain three unique held-out cases")
    return tasks


def _load_study_images(payload: dict[str, object]) -> tuple[dict[str, str], str]:
    build_base = payload.get("build_base_image")
    images_member = payload.get("images")
    if not isinstance(build_base, str) or build_base != BASE_IMAGE:
        raise ContractError("study images must pin the accepted pi runtime base image")
    if not isinstance(images_member, list):
        raise ContractError("study images must be a list")
    images: dict[str, str] = {}
    if len(images_member) != 12:
        raise ContractError("study images must cover exactly twelve cases")
    for entry in images_member:
        if not isinstance(entry, dict) or set(entry) != {"case_id", "image"}:
            raise ContractError("study image entries must be case/image pairs")
        case_id = entry["case_id"]
        image = entry["image"]
        if not isinstance(case_id, str) or not isinstance(image, str):
            raise ContractError("study image entries must be strings")
        marker = "@sha256:"
        if marker not in image:
            raise ContractError("study image references must be digest-pinned")
        validate_sha256(image.rsplit(marker, 1)[1], label="study task image digest")
        images[case_id] = image
    if list(images) != sorted(images) or len(images) != 12:
        raise ContractError("study images must be sorted and unique")
    return images, build_base


def _harbor_package_digest() -> str:
    python_tag = f"python{sys.version_info.major}.{sys.version_info.minor}"
    package_root = Path(sys.prefix) / "lib" / python_tag / "site-packages" / "harbor"
    if not package_root.is_dir():
        raise ContractError("harbor harness package is not installed in the active environment")
    members = [
        (
            path.relative_to(package_root).as_posix(),
            sha256_bytes(read_regular_file_bytes(path)),
        )
        for path in sorted(package_root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]
    if not members:
        raise ContractError("harbor harness package contains no python sources")
    return sha256_bytes(canonical_json_bytes(members))


def default_implementation_artifacts() -> dict[str, dict[str, object]]:
    """The frozen implementation identities beyond the two wheels."""

    return {
        "runtime_adapter": {
            "name": "pi-runtime-configuration",
            "version": PI_VERSION,
            "kind": "policy-bundle",
            "sha256": RUNTIME_CONFIGURATION_SHA256,
        },
        "harness": {
            "name": "harbor",
            "version": HARNESS_VERSION,
            "kind": "source-tree",
            "sha256": _harbor_package_digest(),
        },
    }


def _prompt_configurations(
    prior: CandidateDevelopmentRecord,
    candidate_prompt_path: Path,
) -> tuple[tuple[str, CanonicalAuthoritySource], tuple[str, CanonicalAuthoritySource]]:
    baseline_source = materialize_authority_source(
        BASELINE_PROMPT_SOURCE_ID,
        {"text": BASELINE_PROMPT_TEXT},
    )
    if baseline_source.source_sha256 != BASELINE_PROMPT_SHA256:
        raise ContractError("baseline prompt authority does not match the r9 freeze")
    candidate_source = _load_candidate_prompt(candidate_prompt_path)
    if candidate_source.source_id != CANDIDATE_PROMPT_SOURCE_ID:
        raise ContractError("candidate prompt authority has the wrong source id")
    if candidate_source.source_sha256 != prior.candidate.treatment_sha256:
        raise ContractError("candidate prompt authority does not match the frozen treatment digest")
    return (
        ("baseline", baseline_source),
        ("candidate", candidate_source),
    )


def assemble_p4_study(
    *,
    visible_root: Path,
    heldout_task_paths: tuple[Path, ...],
    images_payload: dict[str, object],
    prior_record_path: Path,
    candidate_prompt_path: Path,
    manifest_path: Path,
    companion_artifact: Mapping[str, object],
    cernora_artifact: Mapping[str, object],
    runtime_adapter_artifact: Mapping[str, object],
    harness_artifact: Mapping[str, object],
) -> P4StudyAssembly:
    """Build every study authority and verify all closed bindings offline."""

    visible_tasks = _load_visible_tasks(visible_root)
    heldout_tasks = _load_heldout_tasks(heldout_task_paths)
    tasks = tuple(sorted((*visible_tasks, *heldout_tasks), key=lambda item: item.case.case_id))
    case_ids = tuple(task.case.case_id for task in tasks)
    if len(case_ids) != 12 or len(set(case_ids)) != 12:
        raise ContractError("study task sets must contain twelve unique cases")

    images, build_base_image = _load_study_images(images_payload)
    if tuple(sorted(images)) != tuple(sorted(case_ids)):
        raise ContractError("study images do not cover exactly the study case set")

    manifest = _load_heldout_manifest(manifest_path)
    commitment_case_ids = tuple(item.case_id for item in manifest.case_commitments)
    revealed_case_ids = tuple(task.case.case_id for task in heldout_tasks)
    if commitment_case_ids != tuple(sorted(revealed_case_ids)) or len(commitment_case_ids) != 3:
        raise ContractError("revealed cases do not match the sealed commitments")

    prior = _load_prior_record(prior_record_path)
    configurations = _prompt_configurations(prior, candidate_prompt_path)

    bootstrap = BootstrapPlan(
        method="case-clustered-paired-bootstrap/v1",
        confidence_basis_points=9500,
        resamples=10000,
        percentile="nearest_rank_closed",
        seed_source="comparison_input_sha256",
    )
    pass_k = PassKPlan(k=3, independent_trials=True)
    specifications = build_controlled_specifications(
        tasks=tasks,
        images=images,
        build_base_image=build_base_image,
        configurations=configurations,
        bootstrap=bootstrap,
        pass_k=pass_k,
        timeout_seconds=AGENT_TIMEOUT_SECONDS,
    )
    statistics = materialize_statistical_policy(bootstrap=bootstrap, pass_k=pass_k)
    split_map = {task.case.case_id: task.split_id for task in tasks}

    plan = materialize_controlled_run_plan(
        {
            "schema_version": "cernora.reference.controlled-run-plan/v2",
            "companion_version": "0.4.2",
            "cernora_version": "0.1.4",
            "connector": {
                "connector_id": "cernora-reference-harbor-pi",
                "connector_version": "2",
                "platform_qualification": "macos-arm64",
            },
            "experiment_specs": [item.model_dump(mode="json") for item in specifications],
            "cases": [
                {
                    "case_id": task.case.case_id,
                    "case_version": task.case.case_version,
                    "task_content_sha256": task.case_sha256,
                }
                for task in tasks
            ],
            "configurations": [
                {"configuration_id": "baseline"},
                {"configuration_id": "candidate"},
            ],
            "cells": [
                {
                    "case_id": item.task.task_id,
                    "configuration_id": item.configuration_id,
                    "experiment_id": item.experiment_id,
                }
                for item in specifications
            ],
            "repetitions": REPETITIONS,
            "pairing_rule": "case-configuration-repetition",
            "planned_trial_count": PLANNED_TRIAL_COUNT,
            "worst_case_attempt_count": MAX_ATTEMPT_COUNT,
            "execution": {
                "concurrency": 1,
                "max_attempt_count": MAX_ATTEMPT_COUNT,
                "max_total_wall_time_seconds": MAX_WALL_SECONDS,
                "token_budget": {
                    "status": "unavailable",
                    "reason": "no-structured-authoritative-source",
                },
                "monetary_budget": {
                    "status": "unavailable",
                    "reason": "no-structured-authoritative-source",
                },
            },
            "analysis": {
                "method": "controlled-comparison",
                "method_version": "m4",
                "aggregate_quality_conclusion": False,
            },
        }
    )
    comparison = materialize_comparison_plan_contract(
        {
            "schema_version": "cernora.reference.comparison-plan/v1",
            "source_run_plan_id": plan.run_plan_id,
            "baseline_configuration_id": "baseline",
            "candidate_configuration_id": "candidate",
            "case_splits": [
                {"case_id": task.case.case_id, "split_id": split_map[task.case.case_id]}
                for task in tasks
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
            "pass_k": statistics.pass_k.model_dump(mode="json") if statistics.pass_k else None,
            "statistical_policy": statistics.model_dump(mode="json"),
        }
    )
    comparison.validate_run_plan(plan)

    case_authorities = [
        {
            "case_id": case_id,
            "split": split_map[case_id],
            "authority_sha256": case_authority_sha256(plan, case_id),
        }
        for case_id in sorted(case_ids)
    ]
    heldout_entries = [item for item in case_authorities if item["split"] == "held-out"]
    baseline_authority = configuration_authority_sha256(plan, "baseline")
    candidate_authority = configuration_authority_sha256(plan, "candidate")

    record = freeze_candidate_development(
        {
            "schema_version": "cernora.reference.candidate-development/v1",
            "baseline": {
                "configuration_id": "baseline",
                "authority_sha256": baseline_authority,
            },
            "candidate": {
                "configuration_id": "candidate",
                "baseline_authority_sha256": baseline_authority,
                "authority_sha256": candidate_authority,
                "treatment_axis": prior.candidate.treatment_axis,
                "treatment_sha256": prior.candidate.treatment_sha256,
            },
            "hypothesis": prior.hypothesis.model_dump(mode="json"),
            "observations": [item.model_dump(mode="json") for item in prior.observations],
        }
    )
    if candidate_continuity_violations(prior, record) != ():
        raise ContractError("re-minted Candidate record drifted from the frozen content")
    if record.development_id == prior.development_id:
        raise ContractError("re-minted Candidate record must change the development identity")

    analysis_policy = materialize_study_analysis_policy(
        {
            "schema_version": "cernora.reference.study-analysis-policy/v1",
            "primary_outcome": "paired-reliable-success-rate-delta",
            "bootstrap_resamples": 10000,
            "confidence_level": "0.95",
            "guardrail_rule": "no-protected-regression",
            "missing_evidence": "inconclusive",
            "claim_source": "held-out-only",
        }
    )
    policy_sha256 = sha256_bytes(canonical_json_bytes(analysis_policy.model_dump(mode="json")))
    commitment = materialize_heldout_commitment(
        {
            "schema_version": "cernora.reference.heldout-commitment/v1",
            "manifest_sha256": sha256_bytes(manifest.canonical_bytes()),
            "case_count": 3,
            "case_commitment_root_sha256": sha256_bytes(canonical_json_bytes(heldout_entries)),
            "reveal_policy_sha256": sha256_bytes(canonical_json_bytes(dict(REVEAL_POLICY_PAYLOAD))),
        }
    )
    lock = materialize_implementation_lock(
        {
            "schema_version": "cernora.reference.implementation-lock/v1",
            "companion": dict(companion_artifact),
            "cernora": dict(cernora_artifact),
            "runtime_adapter": dict(runtime_adapter_artifact),
            "harness": dict(harness_artifact),
            "analysis_policy": {
                "name": "study-analysis-policy",
                "version": "v1",
                "kind": "policy-bundle",
                "sha256": policy_sha256,
            },
        }
    )
    intent = materialize_study_intent(
        {
            "schema_version": "cernora.reference.study-intent/v1",
            "study_kind": STUDY_KIND,
            "candidate_development": record.model_dump(mode="json"),
            "cases": case_authorities,
            "repetitions": REPETITIONS,
            "max_attempt_count": MAX_ATTEMPT_COUNT,
            "max_wall_seconds": MAX_WALL_SECONDS,
            "heldout_commitment": commitment.model_dump(mode="json"),
            "analysis_policy": analysis_policy.model_dump(mode="json"),
            "implementation_lock": lock.model_dump(mode="json"),
        }
    )
    protocol = compile_study_protocol(intent)
    binding = bind_study_run_plan(intent, protocol, plan)
    if len(binding.ordered_trial_slot_ids) != PLANNED_TRIAL_COUNT:
        raise ContractError("study plan does not expand to the planned Trial count")
    return P4StudyAssembly(
        intent=intent,
        protocol=protocol,
        run_plan=plan,
        comparison_plan=comparison,
        development=record,
        binding=binding,
    )


def run_study_ceremony(assembly: P4StudyAssembly, study_root: Path) -> object:
    """Prepare, request, and bind the reveal; stop before start-execution."""

    if study_root.exists() or study_root.is_symlink():
        raise ContractError("study custody root must not already exist")
    if not study_root.parent.is_dir() or study_root.parent.is_symlink():
        raise ContractError("study custody parent must be a real directory")
    prepare_study(assembly.intent, study_root)
    advance_study(
        study_root,
        {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "request-reveal",
        },
    )
    reveal = materialize_heldout_reveal(
        {
            "schema_version": "cernora.reference.heldout-reveal/v1",
            "commitment_id": assembly.intent.heldout_commitment.commitment_id,
            "manifest_sha256": assembly.intent.heldout_commitment.manifest_sha256,
            "cases": [
                item.model_dump(mode="json")
                for item in assembly.intent.cases
                if item.split == "held-out"
            ],
        }
    )
    return advance_study(
        study_root,
        {
            "schema_version": "cernora.reference.advance-directive/v1",
            "action": "bind-reveal",
            "reveal": reveal.model_dump(mode="json"),
        },
    )


def _heldout_task_paths(root: Path) -> tuple[Path, ...]:
    if not root.is_dir() or root.is_symlink():
        raise ContractError("held-out task root must be one real directory")
    entries = sorted(path for path in root.iterdir() if path.is_file() and not path.is_symlink())
    files = {path.name: path for path in entries}
    if (
        len(entries) != 4
        or "reveal-receipt.json" not in files
        or any(not name.endswith(".json") for name in files)
        or len([name for name in files if name.startswith("case-")]) != 3
    ):
        raise ContractError("held-out task root must contain three case JSONs and the receipt")
    return tuple(files[name] for name in sorted(files) if name.startswith("case-"))


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visible-root", type=Path, required=True)
    parser.add_argument("--heldout-task-root", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--prior-record", type=Path, required=True)
    parser.add_argument("--candidate-prompt", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--companion-wheel", type=Path, required=True)
    parser.add_argument("--cernora-wheel", type=Path, required=True)
    parser.add_argument("--expected-companion-sha256", default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--study-root", type=Path)
    parser.add_argument(
        "--run-ceremony",
        action="store_true",
        help="run prepare, request-reveal and bind-reveal after assembling",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    try:
        companion_sha256 = sha256_file(arguments.companion_wheel)
        cernora_sha256 = sha256_file(arguments.cernora_wheel)
        if (
            arguments.expected_companion_sha256 is not None
            and companion_sha256 != arguments.expected_companion_sha256
        ):
            raise ContractError("companion wheel does not match the expected identity")
        if cernora_sha256 != ACCEPTED_CORE_0_1_4_WHEEL_SHA256:
            raise ContractError("cernora wheel does not match the frozen core identity")
        artifacts = default_implementation_artifacts()
        assembly = assemble_p4_study(
            visible_root=arguments.visible_root,
            heldout_task_paths=_heldout_task_paths(arguments.heldout_task_root),
            images_payload=_load_json_object(arguments.images),
            prior_record_path=arguments.prior_record,
            candidate_prompt_path=arguments.candidate_prompt,
            manifest_path=arguments.manifest,
            companion_artifact={
                "name": "cernora-reference-workflow",
                "version": ACCEPTED_COMPANION_VERSION,
                "kind": "wheel",
                "sha256": companion_sha256,
            },
            cernora_artifact={
                "name": "cernora",
                "version": "0.1.4",
                "kind": "wheel",
                "sha256": cernora_sha256,
            },
            runtime_adapter_artifact=artifacts["runtime_adapter"],
            harness_artifact=artifacts["harness"],
        )
        output_dir = arguments.output_dir
        if output_dir.exists() or output_dir.is_symlink():
            raise ContractError("study materialization output must not already exist")
        output_dir.mkdir(parents=True, mode=0o700)
        outputs: dict[str, bytes] = {
            "intent.json": canonical_json_bytes(assembly.intent.model_dump(mode="json")),
            "run-plan.json": assembly.run_plan.canonical_bytes(),
            "comparison-plan.json": assembly.comparison_plan.canonical_bytes(),
            "development-record.json": canonical_json_bytes(
                assembly.development.model_dump(mode="json")
            ),
        }
        for name, payload in outputs.items():
            _exclusive_write(output_dir / name, payload, mode=0o600)
        ceremony: object = None
        if arguments.run_ceremony:
            study_root = arguments.study_root
            if study_root is None:
                study_root = output_dir / "study"
            ceremony = run_study_ceremony(assembly, study_root)
        summary = {
            "intent_id": assembly.intent.intent_id,
            "protocol_id": assembly.protocol.protocol_id,
            "run_plan_id": assembly.run_plan.run_plan_id,
            "comparison_plan_id": assembly.comparison_plan.comparison_plan_id,
            "binding_id": assembly.binding.binding_id,
            "development_id": assembly.development.development_id,
            "heldout_commitment_id": assembly.intent.heldout_commitment.commitment_id,
            "ceremony": (None if ceremony is None else type(ceremony).__name__),
        }
        print(canonical_json_bytes(summary).decode("utf-8"))
    except (ContractError, HeldoutSealError, OSError, ValueError):
        print("error: study materialization failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
