#!/usr/bin/env python3
"""Create and verify the exact private M4 final-plan package."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from cernora import reload_batch_summary_package

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.comparison_plan import ComparisonPlanV1
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
)
from cernora_reference_workflow.heldout_seal import HeldoutManifest, HeldoutRevealReceipt
from cernora_reference_workflow.improvement_loop import CandidateFreeze, verify_candidate_freeze
from cernora_reference_workflow.m4_final_plan import (
    M4ImageAuthoritySet,
    build_m4_final_plans,
)
from cernora_reference_workflow.publication import atomic_publish_directory


def _visible_tasks(root: Path) -> tuple[ControlledTaskAuthority, ...]:
    if not root.is_dir() or root.is_symlink():
        raise ContractError("visible corpus root must be one real directory")
    roots = tuple(
        sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink())
    )
    tasks = tuple(load_visible_task(path) for path in roots)
    if len(tasks) != 6 or {item.split_id for item in tasks} != {"development", "regression"}:
        raise ContractError("visible corpus must contain exact development/regression task sets")
    return tasks


def _task(path: Path) -> ControlledTaskAuthority:
    return ControlledTaskAuthority.from_bytes(read_regular_file_bytes(path))


def create_final_plan_package(
    *,
    visible_root: Path,
    heldout_task_paths: tuple[Path, ...],
    candidate_freeze_path: Path,
    pilot_package_root: Path,
    heldout_manifest_path: Path,
    reveal_receipt_path: Path,
    image_authorities_path: Path,
    output: Path,
) -> tuple[ControlledRunPlanV2, ComparisonPlanV1]:
    """Publish a closed package only after all final authorities verify together."""

    if len(heldout_task_paths) != 3:
        raise ContractError("final Plan requires exactly three held-out task authorities")
    if output.exists() or output.is_symlink():
        raise ContractError("final-plan output must not already exist")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ContractError("final-plan output parent must be one real directory")
    visible_tasks = _visible_tasks(visible_root)
    heldout_tasks = tuple(_task(path) for path in heldout_task_paths)
    tasks = tuple(sorted((*visible_tasks, *heldout_tasks), key=lambda item: item.case.case_id))
    freeze = CandidateFreeze.from_file(candidate_freeze_path)
    pilot = reload_batch_summary_package(pilot_package_root)
    manifest = HeldoutManifest.from_file(heldout_manifest_path)
    reveal = HeldoutRevealReceipt.from_bytes(read_regular_file_bytes(reveal_receipt_path))
    images = M4ImageAuthoritySet.from_file(image_authorities_path)
    plan, comparison = build_m4_final_plans(
        tasks=tasks,
        freeze=freeze,
        image_authorities=images,
    )
    verification = verify_candidate_freeze(
        freeze,
        pilot_package=pilot,
        run_plan=plan,
        comparison_plan=comparison,
        visible_corpus_root=visible_root,
        heldout_manifest=manifest,
        reveal_receipt=reveal,
        task_authorities=tasks,
    )

    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        task_root = staging / "tasks"
        task_root.mkdir()
        files = {
            "comparison-plan.json": comparison.canonical_bytes(),
            "image-authorities.json": images.canonical_bytes(),
            "run-plan.json": plan.canonical_bytes(),
            "verification-receipt.json": canonical_json_bytes(verification.model_dump(mode="json")),
            **{f"tasks/{task.case.case_id}.json": task.canonical_bytes() for task in tasks},
        }
        for relative, content in files.items():
            destination = staging.joinpath(*relative.split("/"))
            destination.write_bytes(content)
        package_payload: dict[str, object] = {
            "schema_version": "cernora.reference.m4-final-plan-package/v1",
            "run_plan_id": plan.run_plan_id,
            "comparison_plan_id": comparison.comparison_plan_id,
            "candidate_freeze_verification_id": verification.verification_id,
            "image_authority_set_id": images.authority_set_id,
            "files": [
                {"path": path, "sha256": sha256_bytes(content)}
                for path, content in sorted(files.items())
            ],
        }
        package_payload["package_id"] = canonical_content_id(package_payload, excluded=frozenset())
        package_bytes = canonical_json_bytes(package_payload)
        (staging / "package.json").write_bytes(package_bytes)

        reloaded_plan = ControlledRunPlanV2.from_file(staging / "run-plan.json")
        reloaded_comparison = ComparisonPlanV1.from_file(staging / "comparison-plan.json")
        reloaded_images = M4ImageAuthoritySet.from_file(staging / "image-authorities.json")
        reloaded_tasks = tuple(
            _task(staging / "tasks" / f"{task.case.case_id}.json") for task in tasks
        )
        if (
            reloaded_plan != plan
            or reloaded_comparison != comparison
            or reloaded_images != images
            or reloaded_tasks != tasks
            or read_regular_file_bytes(staging / "package.json") != package_bytes
        ):
            raise ContractError("staged final-plan package changed during strict reload")
        reloaded_comparison.validate_run_plan(reloaded_plan)
        verify_candidate_freeze(
            freeze,
            pilot_package=pilot,
            run_plan=reloaded_plan,
            comparison_plan=reloaded_comparison,
            visible_corpus_root=visible_root,
            heldout_manifest=manifest,
            reveal_receipt=reveal,
            task_authorities=reloaded_tasks,
        )
        expected = {"package.json", *files}
        if set(closed_regular_tree(staging)) != expected:
            raise ContractError("final-plan package is not a closed regular tree")
        atomic_publish_directory(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return plan, comparison


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visible-root", type=Path, required=True)
    parser.add_argument("--heldout-task", action="append", type=Path, required=True)
    parser.add_argument("--candidate-freeze", type=Path, required=True)
    parser.add_argument("--pilot-package", type=Path, required=True)
    parser.add_argument("--heldout-manifest", type=Path, required=True)
    parser.add_argument("--reveal-receipt", type=Path, required=True)
    parser.add_argument("--image-authorities", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        plan, comparison = create_final_plan_package(
            visible_root=arguments.visible_root,
            heldout_task_paths=tuple(arguments.heldout_task),
            candidate_freeze_path=arguments.candidate_freeze,
            pilot_package_root=arguments.pilot_package,
            heldout_manifest_path=arguments.heldout_manifest,
            reveal_receipt_path=arguments.reveal_receipt,
            image_authorities_path=arguments.image_authorities,
            output=arguments.output,
        )
    except (ContractError, OSError, ValueError):
        print("error: final M4 Plan generation failed", file=sys.stderr)
        return 1
    print(
        canonical_json_bytes(
            {
                "comparison_plan_id": comparison.comparison_plan_id,
                "run_plan_id": plan.run_plan_id,
                "trial_count": len(plan.expand_trial_slots()),
            }
        ).decode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
