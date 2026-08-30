"""Normalize one closed Repeat Runner Execution Pack into a Core BatchInput."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Literal

from cernora import (
    BatchAttempt,
    BatchAttemptResources,
    BatchInput,
    BatchLifecycleRecord,
    BatchPlannedTrial,
    BatchSummary,
    BatchTrial,
    embed_evaluation_package,
    materialize_batch_input,
    summarize_batch,
)

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_execution import (
    verify_controlled_attempt_artifact,
)
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.execution import (
    ClosedFile,
    ControlledTrialResultManifest,
    ExecutionPackManifest,
    TrialEvaluationAvailable,
    TrialResultManifest,
    reload_execution,
    verify_execution_pack,
)
from cernora_reference_workflow.lifecycle import TerminalState
from cernora_reference_workflow.report import AvailableDigest, AvailableMetric, RunReport

NORMALIZER_VERSION = "0.2.1"

LifecycleCategory = Literal[
    "timed_out",
    "interrupted",
    "infrastructure_start_failure",
    "transient_provider_pre_terminal",
    "runtime_pre_terminal_failure",
]


def _lifecycle_category(state: TerminalState) -> LifecycleCategory:
    categories: dict[TerminalState, LifecycleCategory] = {
        "timed-out": "timed_out",
        "interrupted": "interrupted",
        "infrastructure-start-failure": "infrastructure_start_failure",
        "transient-provider-pre-terminal": "transient_provider_pre_terminal",
        "runtime-pre-terminal-failure": "runtime_pre_terminal_failure",
    }
    category = categories.get(state)
    if category is None:
        raise ContractError("an evaluated terminal cannot be normalized as lifecycle-only")
    return category


def _metric_value(metric: object) -> int | None:
    return metric.value if isinstance(metric, AvailableMetric) else None


def _selected_resources(report: RunReport) -> BatchAttemptResources:
    token_metrics = tuple(
        metric
        for metric in (report.diagnostics.input_tokens, report.diagnostics.output_tokens)
        if isinstance(metric, AvailableMetric)
    )
    usage_receipts = {metric.source_receipt_sha256 for metric in token_metrics}
    if len(usage_receipts) > 1:
        raise ContractError("token metrics do not share one authoritative usage receipt")
    return BatchAttemptResources(
        duration_milliseconds=_metric_value(report.diagnostics.duration),
        input_tokens=_metric_value(report.diagnostics.input_tokens),
        output_tokens=_metric_value(report.diagnostics.output_tokens),
        cost_microunits=None,
        usage_receipt_sha256=next(iter(usage_receipts), None),
    )


def _normalized_attempt_id(
    *,
    execution_id: str,
    trial_id: str,
    ordinal: int,
    source_attempt_id: str,
    artifact_kind: str,
    artifact_manifest_sha256: str,
    terminal_sha256: str,
) -> str:
    return canonical_content_id(
        {
            "artifact_kind": artifact_kind,
            "artifact_manifest_sha256": artifact_manifest_sha256,
            "execution_id": execution_id,
            "ordinal": ordinal,
            "source_attempt_id": source_attempt_id,
            "terminal_sha256": terminal_sha256,
            "trial_id": trial_id,
        },
        excluded=frozenset(),
    )


def _root_fingerprint(root: Path) -> tuple[int, int, int, int]:
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise ContractError("Execution Pack root is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink():
        raise ContractError("Execution Pack root must be one real directory")
    return metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns


def _read_bound_pack_file(path: Path, expected: ClosedFile) -> bytes:
    try:
        path_metadata = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ContractError(f"cannot snapshot Execution Pack file: {expected.path}") from exc
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(path_metadata.st_mode)
            or path_metadata.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise ContractError(f"unsafe Execution Pack file during snapshot: {expected.path}")
        data = handle.read()
        finished = os.fstat(handle.fileno())
    if (
        (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        != (
            finished.st_dev,
            finished.st_ino,
            finished.st_size,
            finished.st_mtime_ns,
            finished.st_ctime_ns,
        )
        or len(data) != expected.byte_length
        or sha256_bytes(data) != expected.sha256
    ):
        raise ContractError(f"Execution Pack changed during snapshot: {expected.path}")
    return data


def _snapshot_execution_pack(
    pack_root: Path, snapshot_root: Path
) -> tuple[ExecutionPackManifest, tuple[int, int, int, int]]:
    fingerprint = _root_fingerprint(pack_root)
    manifest = verify_execution_pack(pack_root)
    snapshot_root.mkdir()
    for entry in manifest.files:
        destination = snapshot_root / entry.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_read_bound_pack_file(pack_root / entry.path, entry))
    (snapshot_root / "manifest.json").write_bytes(
        canonical_json_bytes(manifest.model_dump(mode="json"))
    )
    if verify_execution_pack(snapshot_root) != manifest:
        raise ContractError("Execution Pack snapshot identity mismatch")
    return manifest, fingerprint


def _normalize_snapshot(pack_root: Path) -> BatchInput:
    """Normalize one already verified immutable Pack snapshot."""

    execution_root = pack_root / "execution"
    state = reload_execution(execution_root)
    if state.manifest is None or state.checkpoints[-1].status != "completed":
        raise ContractError("Batch normalization requires one completed Execution Pack")

    results = {item.trial_id: item for item in state.trial_results}
    manifests = {item.trial_id: item for item in state.trial_manifests}
    planned_trials: list[BatchPlannedTrial] = []
    trials: list[BatchTrial] = []

    for execution_slot in state.trial_slots.slots:
        slot = execution_slot.slot
        trial_manifest = manifests[execution_slot.trial_id]
        result = results[execution_slot.trial_id]
        planned_trials.append(
            BatchPlannedTrial(
                slot_index=slot.slot_index,
                trial_slot_id=slot.trial_slot_id,
                case_id=slot.case_id,
                configuration_id=slot.configuration_id,
                experiment_id=slot.experiment_id,
                repetition=slot.repetition,
            )
        )

        if isinstance(result, ControlledTrialResultManifest):
            if not isinstance(state.run_plan, ControlledRunPlanV2):
                raise ContractError("controlled Trial result requires a controlled V2 RunPlan")
            controlled_attempts: list[BatchAttempt] = []
            for binding in trial_manifest.attempts:
                if binding.artifact_kind != "controlled-attempt":
                    raise ContractError("controlled Trial contains a non-controlled artifact")
                artifact = verify_controlled_attempt_artifact(
                    execution_root / "attempts" / execution_slot.trial_id / f"{binding.ordinal:04d}"
                )
                attempt = artifact.attempt
                if (
                    binding.attempt_id != attempt.attempt_id
                    or binding.predecessor_attempt_id != attempt.predecessor_attempt_id
                    or binding.source_trial_id != attempt.trial_id
                    or binding.ordinal != attempt.ordinal
                ):
                    raise ContractError(
                        "controlled Attempt contradicts its immutable Trial binding"
                    )
                controlled_attempts.append(
                    BatchAttempt(
                        schema_version="agent.evaluator.batch-attempt/v1",
                        attempt_id=attempt.attempt_id,
                        source_attempt_id=attempt.source_attempt_id,
                        trial_id=attempt.trial_id,
                        ordinal=attempt.ordinal,
                        predecessor_attempt_id=attempt.predecessor_attempt_id,
                        source_manifest_sha256=attempt.source_manifest_sha256,
                        retry_eligible=attempt.retry_eligible,
                        resources=attempt.resources,
                        evaluation=attempt.evaluation,
                        lifecycle=attempt.lifecycle,
                    )
                )
            trials.append(
                BatchTrial(
                    schema_version="agent.evaluator.batch-trial/v1",
                    run_plan_id=state.run_plan.run_plan_id,
                    execution_id=state.record.execution_id,
                    trial_id=execution_slot.trial_id,
                    slot_index=slot.slot_index,
                    trial_slot_id=slot.trial_slot_id,
                    case_id=slot.case_id,
                    configuration_id=slot.configuration_id,
                    experiment_id=slot.experiment_id,
                    repetition=slot.repetition,
                    selected_attempt_id=result.selected_attempt_id,
                    attempts=tuple(controlled_attempts),
                )
            )
            continue

        if not isinstance(result, TrialResultManifest):
            raise ContractError("Execution contains an unknown Trial result contract")
        report = RunReport.from_file(
            execution_root / "results" / execution_slot.trial_id / "run-report.json"
        )

        evaluation = None
        if isinstance(result.evaluation, TrialEvaluationAvailable):
            evaluated_root = (
                execution_root
                / "results"
                / execution_slot.trial_id
                / "offline-evaluation"
                / "evaluated"
            )
            evaluation = embed_evaluation_package(evaluated_root)

        attempts: list[BatchAttempt] = []
        previous_normalized_attempt_id: str | None = None
        for binding, reported in zip(trial_manifest.attempts, report.attempts, strict=True):
            if (
                binding.ordinal != reported.ordinal
                or binding.attempt_id != reported.attempt_id
                or binding.predecessor_attempt_id != reported.predecessor_attempt_id
                or binding.source_trial_id != reported.source_trial_id
                or not isinstance(reported.artifact_manifest_sha256, AvailableDigest)
                or binding.artifact_manifest_sha256 != reported.artifact_manifest_sha256.sha256
            ):
                raise ContractError("Attempt binding contradicts its strict RunReport record")
            normalized_attempt_id = _normalized_attempt_id(
                execution_id=state.record.execution_id,
                trial_id=execution_slot.trial_id,
                ordinal=binding.ordinal,
                source_attempt_id=binding.attempt_id,
                artifact_kind=binding.artifact_kind,
                artifact_manifest_sha256=binding.artifact_manifest_sha256,
                terminal_sha256=binding.terminal_sha256,
            )
            selected = binding.attempt_id == trial_manifest.selected_attempt_id
            if selected and evaluation is not None:
                attempts.append(
                    BatchAttempt(
                        schema_version="agent.evaluator.batch-attempt/v1",
                        attempt_id=normalized_attempt_id,
                        source_attempt_id=binding.attempt_id,
                        trial_id=execution_slot.trial_id,
                        ordinal=binding.ordinal,
                        predecessor_attempt_id=previous_normalized_attempt_id,
                        source_manifest_sha256=binding.artifact_manifest_sha256,
                        retry_eligible=reported.retry_eligible,
                        resources=_selected_resources(report),
                        evaluation=evaluation,
                    )
                )
                previous_normalized_attempt_id = normalized_attempt_id
                continue
            attempts.append(
                BatchAttempt(
                    schema_version="agent.evaluator.batch-attempt/v1",
                    attempt_id=normalized_attempt_id,
                    source_attempt_id=binding.attempt_id,
                    trial_id=execution_slot.trial_id,
                    ordinal=binding.ordinal,
                    predecessor_attempt_id=previous_normalized_attempt_id,
                    source_manifest_sha256=binding.artifact_manifest_sha256,
                    retry_eligible=reported.retry_eligible,
                    resources=(
                        _selected_resources(report) if selected else BatchAttemptResources()
                    ),
                    lifecycle=BatchLifecycleRecord(
                        schema_version="agent.evaluator.batch-lifecycle/v1",
                        category=_lifecycle_category(reported.lifecycle_outcome),
                        retry_eligible=reported.retry_eligible,
                        source_state=reported.lifecycle_outcome,
                        receipt_sha256=binding.terminal_sha256,
                    ),
                )
            )
            previous_normalized_attempt_id = normalized_attempt_id

        trials.append(
            BatchTrial(
                schema_version="agent.evaluator.batch-trial/v1",
                run_plan_id=state.run_plan.run_plan_id,
                execution_id=state.record.execution_id,
                trial_id=execution_slot.trial_id,
                slot_index=slot.slot_index,
                trial_slot_id=slot.trial_slot_id,
                case_id=slot.case_id,
                configuration_id=slot.configuration_id,
                experiment_id=slot.experiment_id,
                repetition=slot.repetition,
                selected_attempt_id=attempts[-1].attempt_id,
                attempts=tuple(attempts),
            )
        )

    if isinstance(state.run_plan, ControlledRunPlanV2) and not any(
        attempt.evaluation is not None for trial in trials for attempt in trial.attempts
    ):
        raise ContractError("controlled Batch publication requires evaluated Trial evidence")

    payload = {
        "schema_version": "agent.evaluator.batch-input/v1",
        "run_plan_id": state.run_plan.run_plan_id,
        "execution_id": state.record.execution_id,
        "execution_status": "completed",
        "budget_status": "within_budget",
        "companion_version": (
            state.run_plan.companion_version
            if isinstance(state.run_plan, ControlledRunPlanV2)
            else NORMALIZER_VERSION
        ),
        "planned_trial_count": len(planned_trials),
        "attempt_count": sum(len(item.attempts) for item in trials),
        "planned_trials": [item.model_dump(mode="json") for item in planned_trials],
        "trials": [item.model_dump(mode="json") for item in trials],
    }
    return materialize_batch_input(payload)


def normalize_execution_pack(pack_root: Path) -> BatchInput:
    """Strictly snapshot and losslessly normalize one completed M1 Execution Pack."""

    with tempfile.TemporaryDirectory(prefix="cernora-pack-snapshot-") as directory:
        snapshot_root = Path(directory) / "pack"
        manifest, fingerprint = _snapshot_execution_pack(pack_root, snapshot_root)
        batch_input = _normalize_snapshot(snapshot_root)
        if (
            verify_execution_pack(pack_root) != manifest
            or _root_fingerprint(pack_root) != fingerprint
        ):
            raise ContractError("Execution Pack changed during normalization")
        return batch_input


def summarize_execution_pack(pack_root: Path, output: Path) -> BatchSummary:
    """Normalize one strict Pack and publish its deterministic Core Batch Summary."""

    return summarize_batch(normalize_execution_pack(pack_root), output)


__all__ = [
    "NORMALIZER_VERSION",
    "normalize_execution_pack",
    "summarize_execution_pack",
]
