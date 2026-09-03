"""Strict completed-export/v1 manifest verification and publication."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
    sha256_file,
    validate_relative_path,
)
from cernora_reference_workflow.experiment_spec import Digest, NonEmpty, StrictContract
from cernora_reference_workflow.lifecycle import TerminalRecord, TerminalState
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.runtime_observation import (
    CONTAINER_CLEANUP_RECEIPT,
    ContainerImageObservation,
    RuntimeBoundaryObservation,
    inspect_runtime_artifacts,
)
from cernora_reference_workflow.runtime_policy import (
    PI_RUNTIME_ENVIRONMENT,
    PI_VERSION,
    RUNTIME_CLEANUP_RECEIPT,
    RUNTIME_POLICY,
    OperatorInterruptReceipt,
)
from cernora_reference_workflow.secrets import require_secret_free
from cernora_reference_workflow.test_runner import (
    ProcessReceipt,
    ResourceReceipt,
    TestPlan,
    TestResults,
    parse_raw_test_output,
)

REQUIRED_PATHS = frozenset(
    {
        "terminal.json",
        "candidate/tree-manifest.json",
        "tests/test-plan.json",
        "tests/test-results.json",
        "tests/stdout.txt",
        "tests/stderr.txt",
        "receipts/process.json",
        "receipts/resources.json",
    }
)
OPTIONAL_RUNTIME_PATHS = frozenset(
    {
        "runtime/harbor-trial-config.json",
        "runtime/harbor-trial-result.json",
        "runtime/trajectory.json",
        "runtime/pi-events.jsonl",
        "runtime/pi-environment.json",
        "runtime/pi-version.txt",
        "runtime/runtime-policy.json",
        "runtime/runtime-cleanup.json",
        "runtime/operator-interrupt.json",
        "runtime/container-cleanup.json",
        "runtime/runtime-boundary-observation.json",
        "runtime/container-image.json",
    }
)


class ExportError(ContractError):
    """A completed export violates its closed integrity contract."""


class FileEntry(StrictContract):
    path: NonEmpty
    media_type: NonEmpty
    byte_length: Annotated[StrictInt, Field(ge=0)]
    sha256: Digest

    @model_validator(mode="after")
    def validate_path(self) -> FileEntry:
        validate_relative_path(self.path)
        if self.path == "manifest.json":
            raise ValueError("manifest.json cannot recursively list itself")
        return self


class CompletedExportManifest(StrictContract):
    schema_version: Literal["cernora.reference.completed-export/v1"]
    experiment_id: Digest
    attempt_id: Digest
    lifecycle_outcome: TerminalState
    exporter_id: Literal["cernora-reference-exporter"]
    exporter_version: Literal["1"]
    source_trial_id: NonEmpty
    test_authority_id: Literal["tiny-calculator-test-runner", "tiny-calculator-v2-test-runner"]
    test_authority_sha256: Digest
    test_plan_sha256: Digest
    pre_candidate_tree_sha256: Digest
    post_candidate_tree_sha256: Digest
    files: Annotated[tuple[FileEntry, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_file_index(self) -> CompletedExportManifest:
        paths = tuple(entry.path for entry in self.files)
        if paths != tuple(sorted(paths)):
            raise ValueError("manifest file entries must be sorted by path")
        if len(set(paths)) != len(paths):
            raise ValueError("manifest file entries must be unique")
        validate_allowlist(set(paths))
        return self


class CandidateTreeFile(StrictContract):
    path: NonEmpty
    byte_length: Annotated[StrictInt, Field(ge=0)]
    sha256: Digest

    @model_validator(mode="after")
    def validate_path(self) -> CandidateTreeFile:
        validate_relative_path(self.path)
        return self


class CandidateTreeManifest(StrictContract):
    schema_version: Literal["cernora.reference.candidate-tree/v1"]
    tree_sha256: Digest
    files: tuple[CandidateTreeFile, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> CandidateTreeManifest:
        paths = tuple(item.path for item in self.files)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("candidate tree files must be uniquely sorted")
        payload = {"files": [item.model_dump(mode="json") for item in self.files]}
        from cernora_reference_workflow.common import sha256_bytes

        if self.tree_sha256 != sha256_bytes(canonical_json_bytes(payload)):
            raise ValueError("candidate tree identity mismatch")
        return self


def media_type_for(path: str) -> str:
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".jsonl"):
        return "application/x-ndjson"
    if path.endswith(".txt"):
        return "text/plain"
    if path.endswith(".py"):
        return "text/x-python"
    if path.endswith(".toml"):
        return "application/toml"
    return "application/octet-stream"


def validate_allowlist(paths: set[str]) -> None:
    missing = REQUIRED_PATHS - paths
    if missing:
        raise ExportError(f"completed export is missing required files: {sorted(missing)}")
    candidate_files = {path for path in paths if path.startswith("candidate/files/")}
    if not candidate_files:
        raise ExportError("completed export must contain candidate files")
    for path in paths:
        allowed = (
            path in REQUIRED_PATHS
            or path in OPTIONAL_RUNTIME_PATHS
            or path.startswith("candidate/files/")
            or (
                path.startswith("runtime/pi-session/")
                and path.endswith(".jsonl")
                and path.count("/") == 2
            )
        )
        if not allowed:
            raise ExportError(f"completed export contains unexpected file: {path}")


def _load_model(path: Path, model: type[StrictContract]) -> StrictContract:
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise ExportError(f"{path.name} must contain a JSON object")
    try:
        parsed = model.model_validate(payload)
    except ValueError as exc:
        raise ExportError(f"invalid {path.name}: {exc}") from exc
    if path.read_bytes() != canonical_json_bytes(parsed.model_dump(mode="json")):
        raise ExportError(f"{path.name} is not canonical JSON")
    return parsed


def verify_completed_export(root: Path) -> CompletedExportManifest:
    files = closed_regular_tree(root)
    if "manifest.json" not in files:
        raise ExportError("completed export is missing manifest.json")
    manifest = _load_model(files["manifest.json"], CompletedExportManifest)
    assert isinstance(manifest, CompletedExportManifest)

    observed_paths = set(files) - {"manifest.json"}
    indexed_paths = {entry.path for entry in manifest.files}
    if observed_paths != indexed_paths:
        missing = sorted(indexed_paths - observed_paths)
        extra = sorted(observed_paths - indexed_paths)
        raise ExportError(f"manifest tree mismatch; missing={missing}, unexpected={extra}")

    for entry in manifest.files:
        path = files[entry.path]
        metadata = path.stat()
        if metadata.st_size != entry.byte_length:
            raise ExportError(f"byte length mismatch for {entry.path}")
        if sha256_file(path) != entry.sha256:
            raise ExportError(f"content digest mismatch for {entry.path}")
        if media_type_for(entry.path) != entry.media_type:
            raise ExportError(f"media type mismatch for {entry.path}")

    runtime_receipts = {
        "runtime/pi-environment.json",
        "runtime/pi-version.txt",
        "runtime/runtime-policy.json",
        "runtime/runtime-cleanup.json",
        "runtime/container-cleanup.json",
        "runtime/runtime-boundary-observation.json",
        "runtime/container-image.json",
    }
    present_runtime_receipts = runtime_receipts & observed_paths
    if present_runtime_receipts and present_runtime_receipts != runtime_receipts:
        raise ExportError("Runtime policy receipts must be present as one complete set")
    if present_runtime_receipts:
        if files["runtime/pi-environment.json"].read_bytes() != canonical_json_bytes(
            PI_RUNTIME_ENVIRONMENT
        ):
            raise ExportError("effective pi runtime environment receipt mismatch")
        if files["runtime/pi-version.txt"].read_text(encoding="utf-8").strip() != PI_VERSION:
            raise ExportError("effective pi runtime version mismatch")
        if files["runtime/runtime-policy.json"].read_bytes() != canonical_json_bytes(
            RUNTIME_POLICY
        ):
            raise ExportError("Runtime policy receipt mismatch")
        if files["runtime/runtime-cleanup.json"].read_bytes() != canonical_json_bytes(
            RUNTIME_CLEANUP_RECEIPT
        ):
            raise ExportError("Runtime cleanup receipt mismatch")
        if files["runtime/container-cleanup.json"].read_bytes() != canonical_json_bytes(
            CONTAINER_CLEANUP_RECEIPT
        ):
            raise ExportError("container cleanup receipt mismatch")
        _load_model(files["runtime/container-image.json"], ContainerImageObservation)
        observation = _load_model(
            files["runtime/runtime-boundary-observation.json"],
            RuntimeBoundaryObservation,
        )
        assert isinstance(observation, RuntimeBoundaryObservation)
        pi_artifact_paths = tuple(
            sorted(
                path
                for path in observed_paths
                if path in {"runtime/trajectory.json", "runtime/pi-events.jsonl"}
                or path.startswith("runtime/pi-session/")
            )
        )
        regenerated = inspect_runtime_artifacts(
            tuple((path, files[path]) for path in pi_artifact_paths),
            effective_config_sha256=sha256_file(files["runtime/pi-environment.json"]),
            effective_features_sha256=sha256_file(files["runtime/pi-version.txt"]),
            runtime_policy_sha256=sha256_file(files["runtime/runtime-policy.json"]),
            runtime_cleanup_sha256=sha256_file(files["runtime/runtime-cleanup.json"]),
            container_cleanup_sha256=sha256_file(files["runtime/container-cleanup.json"]),
        )
        if observation != regenerated:
            raise ExportError("Runtime boundary observation does not match frozen artifacts")

    interruption_path = "runtime/operator-interrupt.json"
    if manifest.lifecycle_outcome == "interrupted":
        if interruption_path not in observed_paths:
            raise ExportError("interrupted lifecycle requires an operator SIGINT receipt")
        _load_model(files[interruption_path], OperatorInterruptReceipt)
    elif interruption_path in observed_paths:
        raise ExportError("operator interruption receipt contradicts lifecycle outcome")

    terminal = _load_model(files["terminal.json"], TerminalRecord)
    assert isinstance(terminal, TerminalRecord)
    if terminal.attempt_id != manifest.attempt_id or terminal.state != manifest.lifecycle_outcome:
        raise ExportError("terminal record does not match manifest lifecycle binding")
    if sha256_file(files["tests/test-plan.json"]) != manifest.test_plan_sha256:
        raise ExportError("test-plan digest does not match manifest")
    plan = _load_model(files["tests/test-plan.json"], TestPlan)
    results = _load_model(files["tests/test-results.json"], TestResults)
    process = _load_model(files["receipts/process.json"], ProcessReceipt)
    _load_model(files["receipts/resources.json"], ResourceReceipt)
    assert isinstance(plan, TestPlan)
    assert isinstance(results, TestResults)
    assert isinstance(process, ProcessReceipt)
    if plan.authority_sha256 != manifest.test_authority_sha256:
        raise ExportError("Test Runner authority digest does not match manifest")
    if results.test_plan_sha256 != manifest.test_plan_sha256:
        raise ExportError("test results do not bind the frozen test plan")
    if results.test_source_sha256 != plan.test_source_sha256:
        raise ExportError("test results do not bind the frozen test source")
    if process.argv != plan.command or process.working_directory != plan.working_directory:
        raise ExportError("process receipt does not match the frozen test command")
    if process.exit_code != results.exit_code or process.termination != results.termination:
        raise ExportError("process receipt contradicts structured test results")
    if process.stdout_sha256 != sha256_file(files["tests/stdout.txt"]):
        raise ExportError("process stdout digest mismatch")
    if process.stderr_sha256 != sha256_file(files["tests/stderr.txt"]):
        raise ExportError("process stderr digest mismatch")
    if process.termination in {"exited", "runner-error"}:
        try:
            raw_results = parse_raw_test_output(files["tests/stdout.txt"].read_bytes())
        except ContractError as exc:
            raise ExportError(f"invalid raw Test Runner receipt: {exc}") from exc
        if (
            raw_results.termination != results.termination
            or raw_results.tests != results.tests
            or raw_results.runner_error != results.runner_error
        ):
            raise ExportError("structured test results contradict raw Test Runner stdout")
    elif files["tests/stdout.txt"].read_bytes():
        raise ExportError("non-executed Test Runner receipt must have empty stdout")
    changed_within_policy = set(results.changed_paths).issubset(set(plan.allowed_paths))
    if results.protected_paths_unchanged is not changed_within_policy:
        raise ExportError("protected-path verdict contradicts the changed-path receipt")
    if (
        results.pre_candidate_tree_sha256 != manifest.pre_candidate_tree_sha256
        or results.post_candidate_tree_sha256 != manifest.post_candidate_tree_sha256
    ):
        raise ExportError("test receipt candidate-tree binding mismatch")
    if manifest.lifecycle_outcome == "completed" and results.verdict != "pass":
        raise ExportError("completed lifecycle requires a passing authoritative receipt")
    if manifest.lifecycle_outcome == "behavioral-failure" and results.verdict != "fail":
        raise ExportError("behavioral-failure lifecycle requires a failing authoritative receipt")

    tree = _load_model(files["candidate/tree-manifest.json"], CandidateTreeManifest)
    assert isinstance(tree, CandidateTreeManifest)
    if tree.tree_sha256 != manifest.post_candidate_tree_sha256:
        raise ExportError("candidate tree digest does not match manifest")
    tree_paths = {f"candidate/files/{item.path}" for item in tree.files}
    candidate_paths = {path for path in observed_paths if path.startswith("candidate/files/")}
    if tree_paths != candidate_paths:
        raise ExportError("candidate tree manifest does not close the candidate file set")
    for item in tree.files:
        candidate_path = files[f"candidate/files/{item.path}"]
        if (
            candidate_path.stat().st_size != item.byte_length
            or sha256_file(candidate_path) != item.sha256
        ):
            raise ExportError(f"candidate tree content mismatch for {item.path}")

    require_secret_free(root)
    return manifest


def publish_completed_export(
    staging: Path,
    destination: Path,
    *,
    manifest_fields: dict[str, object],
) -> CompletedExportManifest:
    """Hash, scan, validate, and atomically publish one private staging tree."""

    tree = closed_regular_tree(staging)
    if "manifest.json" in tree:
        raise ExportError("staging must not contain a pre-existing manifest")
    validate_allowlist(set(tree))
    entries = tuple(
        FileEntry(
            path=relative,
            media_type=media_type_for(relative),
            byte_length=path.stat().st_size,
            sha256=sha256_file(path),
        )
        for relative, path in tree.items()
    )
    payload = dict(manifest_fields)
    payload["files"] = [entry.model_dump(mode="json") for entry in entries]
    try:
        manifest = CompletedExportManifest.model_validate(payload)
    except ValueError as exc:
        raise ExportError(f"invalid manifest fields: {exc}") from exc
    (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest.model_dump(mode="json")))
    require_secret_free(staging)
    verify_completed_export(staging)
    atomic_publish_directory(staging, destination)
    return manifest
