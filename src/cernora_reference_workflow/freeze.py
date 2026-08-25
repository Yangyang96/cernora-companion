"""Freeze one Harbor trial capture into the closed completed-export/v1 boundary."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cernora_reference_workflow.common import (
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
    require_regular_file,
    sha256_bytes,
    sha256_file,
    validate_relative_path,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.export import (
    CandidateTreeFile,
    CandidateTreeManifest,
    ExportError,
    publish_completed_export,
)
from cernora_reference_workflow.lifecycle import TerminalRecord, TerminalState
from cernora_reference_workflow.test_runner import (
    ProcessReceipt,
    ResourceReceipt,
    TestPlan,
    TestResults,
    parse_raw_test_output,
)

BASELINE_CANDIDATE_PATHS = ("pyproject.toml", "src/calc.py")
RequestedState = Literal[
    "completed",
    "timed-out",
    "interrupted",
    "infrastructure-start-failure",
    "transient-provider-pre-terminal",
]


@dataclass(frozen=True)
class AttemptCapture:
    """Private physical inputs; absolute paths never enter portable evidence."""

    source_trial_id: str
    candidate_root: Path
    test_stdout: Path
    test_stderr: Path
    test_exit_code: int | None
    resource_receipt: ResourceReceipt
    runtime_files: tuple[tuple[str, Path], ...] = ()


def _candidate_tree(
    root: Path,
    *,
    selected_paths: tuple[str, ...] | None = None,
) -> CandidateTreeManifest:
    files = closed_regular_tree(root)
    if selected_paths is not None:
        expected = set(selected_paths)
        missing = expected - set(files)
        if missing:
            raise ExportError(f"baseline candidate is missing files: {sorted(missing)}")
        files = {path: files[path] for path in selected_paths}
    entries = tuple(
        CandidateTreeFile(
            path=relative,
            byte_length=path.stat().st_size,
            sha256=sha256_file(path),
        )
        for relative, path in sorted(files.items())
    )
    identity_payload = {"files": [entry.model_dump(mode="json") for entry in entries]}
    return CandidateTreeManifest(
        schema_version="cernora.reference.candidate-tree/v1",
        tree_sha256=sha256_bytes(canonical_json_bytes(identity_payload)),
        files=entries,
    )


def _tree_changes(
    baseline: CandidateTreeManifest,
    candidate: CandidateTreeManifest,
) -> tuple[str, ...]:
    before = {item.path: item.sha256 for item in baseline.files}
    after = {item.path: item.sha256 for item in candidate.files}
    return tuple(
        sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    )


def _copy_candidate(source: Path, destination: Path) -> CandidateTreeManifest:
    files = closed_regular_tree(source)
    for relative, path in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return _candidate_tree(destination)


def _load_plan(path: Path) -> TestPlan:
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise ExportError("frozen Test Plan must contain one JSON object")
    try:
        plan = TestPlan.model_validate(payload)
    except ValueError as exc:
        raise ExportError(f"invalid frozen Test Plan: {exc}") from exc
    if path.read_bytes() != canonical_json_bytes(plan.model_dump(mode="json")):
        raise ExportError("frozen Test Plan is not canonical JSON")
    return plan


def _attempt_id(
    *,
    spec: ExperimentSpec,
    source_trial_id: str,
    requested_state: RequestedState,
    predecessor_attempt_id: str | None,
    post_tree_sha256: str,
    stdout_sha256: str,
    stderr_sha256: str,
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "experiment_id": spec.experiment_id,
                "post_candidate_tree_sha256": post_tree_sha256,
                "predecessor_attempt_id": predecessor_attempt_id,
                "requested_state": requested_state,
                "source_trial_id": source_trial_id,
                "stderr_sha256": stderr_sha256,
                "stdout_sha256": stdout_sha256,
            }
        )
    )


def freeze_attempt(
    *,
    spec: ExperimentSpec,
    capture: AttemptCapture,
    baseline_root: Path,
    test_plan_path: Path,
    requested_state: RequestedState,
    predecessor_attempt_id: str | None,
    destination: Path,
) -> TerminalRecord:
    """Validate, close, scan, and atomically publish one immutable attempt export."""

    if (
        not capture.source_trial_id
        or "/" in capture.source_trial_id
        or "\\" in capture.source_trial_id
    ):
        raise ExportError("source trial ID must be a non-path identifier")
    if destination.exists() or destination.is_symlink():
        raise ExportError("completed export destination must not already exist")
    if not destination.parent.is_dir():
        raise ExportError("completed export parent must already exist")

    plan = _load_plan(test_plan_path)
    plan_sha256 = sha256_file(test_plan_path)
    if (
        plan_sha256 != spec.test_runner.test_plan_sha256
        or plan.authority_sha256 != spec.test_runner.authority_sha256
        or plan.test_source_sha256 != spec.test_runner.test_source_sha256
        or plan.command != spec.test_runner.command
    ):
        raise ExportError("ExperimentSpec does not bind the frozen Test Runner authority")

    baseline = _candidate_tree(baseline_root, selected_paths=BASELINE_CANDIDATE_PATHS)
    stdout = require_regular_file(capture.test_stdout)
    stderr = require_regular_file(capture.test_stderr)
    del stdout, stderr
    stdout_bytes = capture.test_stdout.read_bytes()
    stderr_bytes = capture.test_stderr.read_bytes()
    try:
        stderr_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExportError("Test Runner stderr is not UTF-8") from exc

    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        post = _copy_candidate(capture.candidate_root, staging / "candidate/files")
        changes = _tree_changes(baseline, post)
        protected_unchanged = set(changes).issubset(set(plan.allowed_paths))

        if capture.test_exit_code is None:
            if stdout_bytes:
                raise ExportError("non-executed Test Runner must have empty stdout")
            if requested_state not in {"timed-out", "interrupted"}:
                raise ExportError("only timeout or interruption may omit Test Runner execution")
            result_termination: Literal["timed-out", "interrupted"] = (
                "timed-out" if requested_state == "timed-out" else "interrupted"
            )
            test_results = TestResults(
                schema_version="cernora.reference.test-results/v1",
                test_plan_sha256=plan_sha256,
                test_source_sha256=plan.test_source_sha256,
                termination=result_termination,
                exit_code=None,
                tests=(),
                runner_error=f"test-runner-not-executed:{requested_state}",
                pre_candidate_tree_sha256=baseline.tree_sha256,
                post_candidate_tree_sha256=post.tree_sha256,
                changed_paths=changes,
                protected_paths_unchanged=protected_unchanged,
            )
        else:
            raw = parse_raw_test_output(stdout_bytes)
            if tuple(item.test_id for item in raw.tests) != plan.test_ids:
                raise ExportError("Test Runner output does not match the frozen Test Plan cases")
            test_results = TestResults(
                schema_version="cernora.reference.test-results/v1",
                test_plan_sha256=plan_sha256,
                test_source_sha256=plan.test_source_sha256,
                termination=raw.termination,
                exit_code=capture.test_exit_code,
                tests=raw.tests,
                runner_error=raw.runner_error,
                pre_candidate_tree_sha256=baseline.tree_sha256,
                post_candidate_tree_sha256=post.tree_sha256,
                changed_paths=changes,
                protected_paths_unchanged=protected_unchanged,
            )

        lifecycle: TerminalState = requested_state
        if requested_state == "completed":
            if test_results.verdict == "pass":
                lifecycle = "completed"
            elif test_results.verdict == "fail":
                lifecycle = "behavioral-failure"
            else:
                raise ExportError("completed Agent attempt has inconclusive test evidence")

        attempt_id = _attempt_id(
            spec=spec,
            source_trial_id=capture.source_trial_id,
            requested_state=requested_state,
            predecessor_attempt_id=predecessor_attempt_id,
            post_tree_sha256=post.tree_sha256,
            stdout_sha256=sha256_bytes(stdout_bytes),
            stderr_sha256=sha256_bytes(stderr_bytes),
        )
        reasons = {
            "completed": "authoritative-test-pass",
            "behavioral-failure": "authoritative-test-fail",
            "timed-out": "agent-timeout",
            "interrupted": "operator-interruption",
            "infrastructure-start-failure": "runtime-start-failure",
            "transient-provider-pre-terminal": "transient-provider-pre-terminal",
        }
        terminal = TerminalRecord(
            schema_version="cernora.reference.terminal/v1",
            attempt_id=attempt_id,
            state=lifecycle,
            reason=reasons[lifecycle],
            retry_eligible=lifecycle
            in {"infrastructure-start-failure", "transient-provider-pre-terminal"},
            predecessor_attempt_id=predecessor_attempt_id,
        )
        process = ProcessReceipt(
            schema_version="cernora.reference.process-receipt/v1",
            argv=plan.command,
            working_directory=plan.working_directory,
            exit_code=capture.test_exit_code,
            termination=test_results.termination,
            stdout_sha256=sha256_bytes(stdout_bytes),
            stderr_sha256=sha256_bytes(stderr_bytes),
        )

        (staging / "candidate/tree-manifest.json").write_bytes(
            canonical_json_bytes(post.model_dump(mode="json"))
        )
        (staging / "terminal.json").write_bytes(
            canonical_json_bytes(terminal.model_dump(mode="json"))
        )
        (staging / "tests").mkdir()
        (staging / "tests/test-plan.json").write_bytes(test_plan_path.read_bytes())
        (staging / "tests/test-results.json").write_bytes(
            canonical_json_bytes(test_results.model_dump(mode="json"))
        )
        (staging / "tests/stdout.txt").write_bytes(stdout_bytes)
        (staging / "tests/stderr.txt").write_bytes(stderr_bytes)
        (staging / "receipts").mkdir()
        (staging / "receipts/process.json").write_bytes(
            canonical_json_bytes(process.model_dump(mode="json"))
        )
        (staging / "receipts/resources.json").write_bytes(
            canonical_json_bytes(capture.resource_receipt.model_dump(mode="json"))
        )
        for relative, source in capture.runtime_files:
            validate_relative_path(relative)
            if not relative.startswith("runtime/"):
                raise ExportError("Runtime diagnostic must stay below runtime/")
            require_regular_file(source)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

        publish_completed_export(
            staging,
            destination,
            manifest_fields={
                "schema_version": "cernora.reference.completed-export/v1",
                "experiment_id": spec.experiment_id,
                "attempt_id": attempt_id,
                "lifecycle_outcome": lifecycle,
                "exporter_id": "cernora-reference-exporter",
                "exporter_version": "1",
                "source_trial_id": capture.source_trial_id,
                "test_authority_id": plan.authority_id,
                "test_authority_sha256": plan.authority_sha256,
                "test_plan_sha256": plan_sha256,
                "pre_candidate_tree_sha256": baseline.tree_sha256,
                "post_candidate_tree_sha256": post.tree_sha256,
            },
        )
        return terminal
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


__all__ = ["AttemptCapture", "freeze_attempt"]
