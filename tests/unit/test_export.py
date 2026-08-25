from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes, sha256_file
from cernora_reference_workflow.export import (
    CandidateTreeFile,
    ExportError,
    publish_completed_export,
    verify_completed_export,
)
from cernora_reference_workflow.publication import PublicationError
from cernora_reference_workflow.runtime_observation import (
    CONTAINER_CLEANUP_RECEIPT,
    ContainerImageObservation,
    inspect_runtime_artifacts,
)
from cernora_reference_workflow.runtime_policy import (
    RUNTIME_CLEANUP_RECEIPT,
    RUNTIME_POLICY,
    TELEMETRY_CONFIG_TOML,
)
from cernora_reference_workflow.test_runner import TEST_IDS
from cernora_reference_workflow.test_runner import TestPlan as FrozenTestPlan

DIGEST = "0" * 64
ATTEMPT = "a" * 64
TEST_SOURCE_SHA256 = "d02000a75cd10d97fb691368ac80cb9c1187e1af31bf2cc591423fe3f2407dd4"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def materialize_staging(root: Path) -> dict[str, object]:
    candidate = root / "candidate/files/src/calc.py"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tree_file = CandidateTreeFile(
        path="src/calc.py",
        byte_length=candidate.stat().st_size,
        sha256=sha256_file(candidate),
    )
    tree_payload = {"files": [tree_file.model_dump(mode="json")]}
    tree_sha = sha256_bytes(canonical_json_bytes(tree_payload))
    write_json(
        root / "candidate/tree-manifest.json",
        {
            "schema_version": "cernora.reference.candidate-tree/v1",
            "tree_sha256": tree_sha,
            **tree_payload,
        },
    )
    write_json(
        root / "terminal.json",
        {
            "schema_version": "cernora.reference.terminal/v1",
            "attempt_id": ATTEMPT,
            "state": "completed",
            "reason": "fixture",
            "retry_eligible": False,
            "predecessor_attempt_id": None,
        },
    )
    plan_payload = {
        "schema_version": "cernora.reference.test-plan/v1",
        "authority_id": "tiny-calculator-test-runner",
        "authority_version": "1",
        "command": ["python", "/tests/run_tests.py", "--candidate-root", "/workspace"],
        "working_directory": "/workspace",
        "test_ids": list(TEST_IDS),
        "test_source_sha256": TEST_SOURCE_SHA256,
        "allowed_paths": ["src/calc.py"],
        "protected_paths": ["pyproject.toml", "tests"],
    }
    write_json(root / "tests/test-plan.json", plan_payload)
    test_plan_sha256 = sha256_file(root / "tests/test-plan.json")
    expected_values = (-1, -5, 5, 4)
    test_cases = [
        {
            "test_id": test_id,
            "category": "fail-to-pass" if index < 2 else "pass-to-pass",
            "passed": True,
            "expected": expected_values[index],
            "actual": expected_values[index],
            "error": None,
        }
        for index, test_id in enumerate(TEST_IDS)
    ]
    write_json(
        root / "tests/test-results.json",
        {
            "schema_version": "cernora.reference.test-results/v1",
            "test_plan_sha256": test_plan_sha256,
            "test_source_sha256": TEST_SOURCE_SHA256,
            "termination": "exited",
            "exit_code": 0,
            "tests": test_cases,
            "runner_error": None,
            "pre_candidate_tree_sha256": DIGEST,
            "post_candidate_tree_sha256": tree_sha,
            "changed_paths": ["src/calc.py"],
            "protected_paths_unchanged": True,
        },
    )
    raw_stdout = {
        "schema_version": "cernora.reference.test-results/v1",
        "termination": "exited",
        "tests": test_cases,
        "runner_error": None,
    }
    (root / "tests/stdout.txt").write_bytes(canonical_json_bytes(raw_stdout) + b"\n")
    (root / "tests/stderr.txt").write_text("", encoding="utf-8")
    write_json(
        root / "receipts/process.json",
        {
            "schema_version": "cernora.reference.process-receipt/v1",
            "argv": plan_payload["command"],
            "working_directory": "/workspace",
            "exit_code": 0,
            "termination": "exited",
            "stdout_sha256": sha256_file(root / "tests/stdout.txt"),
            "stderr_sha256": sha256_file(root / "tests/stderr.txt"),
        },
    )
    write_json(
        root / "receipts/resources.json",
        {
            "schema_version": "cernora.reference.resource-receipt/v1",
            "duration_milliseconds": 1,
            "peak_memory_bytes": None,
            "cpu_milliseconds": None,
        },
    )
    plan = FrozenTestPlan.model_validate(plan_payload)
    return {
        "schema_version": "cernora.reference.completed-export/v1",
        "experiment_id": DIGEST,
        "attempt_id": ATTEMPT,
        "lifecycle_outcome": "completed",
        "exporter_id": "cernora-reference-exporter",
        "exporter_version": "1",
        "source_trial_id": "fixture-trial",
        "test_authority_id": "tiny-calculator-test-runner",
        "test_authority_sha256": plan.authority_sha256,
        "test_plan_sha256": test_plan_sha256,
        "pre_candidate_tree_sha256": DIGEST,
        "post_candidate_tree_sha256": tree_sha,
    }


def test_export_publication_is_closed_verified_and_no_replace(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "completed"
    fields = materialize_staging(staging)
    manifest = publish_completed_export(staging, destination, manifest_fields=fields)
    assert verify_completed_export(destination) == manifest
    assert not staging.exists()

    second = tmp_path / "second-staging"
    fields = materialize_staging(second)
    with pytest.raises(PublicationError, match="must not already exist"):
        publish_completed_export(second, destination, manifest_fields=fields)
    assert second.exists()


def _write_runtime_policy_receipts(root: Path) -> None:
    runtime = root / "runtime"
    runtime.mkdir()
    (runtime / "codex-effective-config.toml").write_text(
        TELEMETRY_CONFIG_TOML,
        encoding="utf-8",
    )
    (runtime / "codex-effective-features.txt").write_text(
        "plugins stable false\nunified_exec stable true\n",
        encoding="utf-8",
    )
    write_json(runtime / "runtime-policy.json", RUNTIME_POLICY)
    write_json(runtime / "runtime-cleanup.json", RUNTIME_CLEANUP_RECEIPT)
    write_json(runtime / "container-cleanup.json", CONTAINER_CLEANUP_RECEIPT)
    write_json(
        runtime / "container-image.json",
        ContainerImageObservation(
            schema_version="cernora.reference.container-image-observation/v1",
            task_image_reference=f"cernora-reference/test@sha256:{DIGEST}",
            observed_image_id=f"sha256:{DIGEST}",
            matched=True,
        ).model_dump(mode="json"),
    )
    observation = inspect_runtime_artifacts(
        (),
        effective_config_sha256=sha256_file(runtime / "codex-effective-config.toml"),
        effective_features_sha256=sha256_file(runtime / "codex-effective-features.txt"),
        runtime_policy_sha256=sha256_file(runtime / "runtime-policy.json"),
        runtime_cleanup_sha256=sha256_file(runtime / "runtime-cleanup.json"),
        container_cleanup_sha256=sha256_file(runtime / "container-cleanup.json"),
    )
    write_json(runtime / "runtime-boundary-observation.json", observation.model_dump(mode="json"))


def test_complete_observed_runtime_receipt_set_is_verified(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "completed"
    fields = materialize_staging(staging)
    _write_runtime_policy_receipts(staging)
    manifest = publish_completed_export(staging, destination, manifest_fields=fields)
    assert verify_completed_export(destination) == manifest


def test_observed_runtime_feature_state_mismatch_is_rejected(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "completed"
    fields = materialize_staging(staging)
    _write_runtime_policy_receipts(staging)
    (staging / "runtime/codex-effective-features.txt").write_text(
        "plugins stable true\nunified_exec stable true\n",
        encoding="utf-8",
    )
    with pytest.raises(ExportError, match="plugin feature state mismatch"):
        publish_completed_export(staging, destination, manifest_fields=fields)


def test_partial_runtime_receipt_set_is_rejected(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "completed"
    fields = materialize_staging(staging)
    runtime = staging / "runtime"
    runtime.mkdir()
    (runtime / "codex-effective-config.toml").write_text(
        TELEMETRY_CONFIG_TOML,
        encoding="utf-8",
    )
    with pytest.raises(ExportError, match="one complete set"):
        publish_completed_export(staging, destination, manifest_fields=fields)


@pytest.mark.parametrize("operator_receipt", (None, "malformed"))
def test_interrupted_export_requires_strict_operator_receipt(
    tmp_path: Path,
    operator_receipt: str | None,
) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "completed"
    fields = materialize_staging(staging)
    fields["lifecycle_outcome"] = "interrupted"
    terminal = {
        "schema_version": "cernora.reference.terminal/v1",
        "attempt_id": ATTEMPT,
        "state": "interrupted",
        "reason": "operator-interruption",
        "retry_eligible": False,
        "predecessor_attempt_id": None,
    }
    write_json(staging / "terminal.json", terminal)
    if operator_receipt is not None:
        write_json(
            staging / "runtime/operator-interrupt.json",
            {
                "schema_version": "cernora.reference.operator-interrupt/v1",
                "operator_signal": "SIGTERM",
                "target": "active-codex-process",
                "verified_signal_count": 1,
            },
        )
    expected = "requires an operator SIGINT receipt" if operator_receipt is None else "invalid"
    with pytest.raises(ExportError, match=expected):
        publish_completed_export(staging, destination, manifest_fields=fields)


@pytest.mark.parametrize("mutation", ("missing", "extra", "digest", "terminal", "secret"))
def test_export_mutations_fail_closed(tmp_path: Path, mutation: str) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "completed"
    fields = materialize_staging(staging)
    publish_completed_export(staging, destination, manifest_fields=fields)
    if mutation == "missing":
        (destination / "tests/test-results.json").unlink()
    elif mutation == "extra":
        (destination / "unexpected.txt").write_text("x", encoding="utf-8")
    elif mutation == "digest":
        (destination / "tests/stdout.txt").write_text("tampered", encoding="utf-8")
    elif mutation == "terminal":
        write_json(
            destination / "terminal.json",
            {
                "schema_version": "cernora.reference.terminal/v1",
                "attempt_id": ATTEMPT,
                "state": "interrupted",
                "reason": "tampered",
                "retry_eligible": False,
                "predecessor_attempt_id": None,
            },
        )
    else:
        (destination / "tests/stdout.txt").write_text(
            "Authorization: Bearer fake_" + "A" * 32,
            encoding="utf-8",
        )
    with pytest.raises((ExportError, ValueError)):
        verify_completed_export(destination)
