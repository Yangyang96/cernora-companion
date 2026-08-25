"""Pure offline completed-export/v1 to Cernora EvidenceBundle v2 adapter."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from cernora import AdaptedBundle, Case, CaseProfile, CompletedExport, EvidenceBundleV2, Profile

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    load_json_file,
    sha256_bytes,
)
from cernora_reference_workflow.export import (
    CompletedExportManifest,
    ExportError,
    verify_completed_export,
)
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.test_runner import ProcessReceipt, TestResults

PRODUCER_ID = "cernora-reference-workflow"
PRODUCER_VERSION = "1"


class AdapterError(ContractError):
    """The frozen export cannot be normalized without inventing evidence."""


def _model_payload(path: Path, model: type[ProcessReceipt] | type[TestResults]) -> Any:
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise AdapterError(f"{path.name} must contain a JSON object")
    try:
        return model.model_validate(payload)
    except ValueError as exc:
        raise AdapterError(f"invalid {path.name}: {exc}") from exc


def _canonical_identity(model: object) -> str:
    model_dump = getattr(model, "model_dump", None)
    if not callable(model_dump):
        raise AdapterError("Profile authority is not a public strict model")
    return sha256_bytes(canonical_json_bytes(model_dump(mode="json", exclude_none=False)))


def _receipt_digest(payload: dict[str, object]) -> str:
    return sha256_bytes(canonical_json_bytes(payload))


def _artifact(artifact_id: str, path: str, content: bytes, media_type: str) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "path": path,
        "sha256": sha256_bytes(content),
        "size_bytes": len(content),
        "media_type": media_type,
    }


def _pointer(artifact: dict[str, object]) -> dict[str, object]:
    return {"artifact_id": artifact["artifact_id"], "sha256": artifact["sha256"]}


class ReferenceCodingAdapter:
    """Normalize one verified immutable export without Runtime, shell, Git, or network access."""

    def __init__(self, profile: Profile) -> None:
        self._profile = profile

    def adapt(self, completed_export: CompletedExport, output: Path) -> AdaptedBundle:
        root = completed_export.root.resolve()
        output_parent = output.parent.resolve()
        output_resolved = output_parent / output.name
        if output.exists():
            raise AdapterError("adapter output must not already exist")
        if not output_parent.is_dir():
            raise AdapterError("adapter output parent must already exist")
        if (
            root == output_resolved
            or root in output_resolved.parents
            or output_resolved in root.parents
        ):
            raise AdapterError("completed export and adapter output must not overlap")

        try:
            manifest = verify_completed_export(root)
        except ExportError as exc:
            raise AdapterError(f"completed export verification failed: {exc}") from exc

        authority = self._profile.authority
        matching_cases = tuple(
            case
            for case in authority.cases
            if case.input.parameters.get("test_plan_sha256") == manifest.test_plan_sha256
            and case.input.parameters.get("test_authority_id") == manifest.test_authority_id
        )
        if len(matching_cases) != 1:
            raise AdapterError(
                "Profile must contain exactly one Case for the Test Runner authority"
            )
        case = matching_cases[0]

        process = _model_payload(root / "receipts/process.json", ProcessReceipt)
        results = _model_payload(root / "tests/test-results.json", TestResults)
        assert isinstance(process, ProcessReceipt)
        assert isinstance(results, TestResults)

        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output_parent))
        try:
            payload = self._build_bundle_payload(root, manifest, authority, case, process, results)
            bundle = EvidenceBundleV2.model_validate(payload)
            bundle_bytes = canonical_json_bytes(bundle.model_dump(mode="json", exclude_none=False))
            (staging / "bundle.json").write_bytes(bundle_bytes)
            self._write_artifacts(staging, root, process, manifest.lifecycle_outcome)
            atomic_publish_directory(staging, output_resolved)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return AdaptedBundle(bundle_path=output_resolved / "bundle.json")

    def _build_bundle_payload(
        self,
        root: Path,
        manifest: CompletedExportManifest,
        authority: CaseProfile,
        case: Case,
        process: ProcessReceipt,
        results: TestResults,
    ) -> dict[str, object]:
        lifecycle_outcome = manifest.lifecycle_outcome
        experiment_id = manifest.experiment_id
        attempt_id = manifest.attempt_id
        profile_id = authority.profile_id
        profile_version = authority.profile_version

        artifacts: list[dict[str, object]] = []
        actions: list[dict[str, object]] = []
        if process.termination == "exited" and process.exit_code is not None:
            results_content = (root / "tests/test-results.json").read_bytes()
            stderr_content = (root / "tests/stderr.txt").read_bytes()
            stderr_content.decode("utf-8")
            results_artifact = _artifact(
                "test-results",
                "evidence/test-results.json",
                results_content,
                "application/json",
            )
            stderr_artifact = _artifact(
                "test-stderr",
                "evidence/test-stderr.txt",
                stderr_content,
                "text/plain; charset=utf-8",
            )
            action_payload: dict[str, object] = {
                "sequence": 0,
                "invocation_id": "test-runner-1",
                "tool": "run_frozen_test_plan",
                "argv": process.argv,
                "result": {
                    "status": "completed" if process.exit_code == 0 else "failed",
                    "exit_code": process.exit_code,
                    "committed": True,
                    "delivered": True,
                    "stdout_artifact": _pointer(results_artifact),
                    "stderr_artifact": _pointer(stderr_artifact),
                },
                "previous_receipt_sha256": None,
            }
            action_payload["receipt_sha256"] = _receipt_digest(action_payload)
            actions.append(action_payload)
            artifacts.extend((results_artifact, stderr_artifact))

        terminal: dict[str, object]
        infrastructure: dict[str, object] = {"status": "valid", "failure": None}
        if lifecycle_outcome in {"completed", "behavioral-failure"}:
            terminal_content = (root / "terminal.json").read_bytes()
            terminal_text = terminal_content.decode("utf-8")
            terminal_artifact = _artifact(
                "terminal-record",
                "evidence/terminal.json",
                terminal_content,
                "application/json",
            )
            artifacts.append(terminal_artifact)
            terminal = {
                "status": "completed",
                "answer": {
                    "content": terminal_text,
                    "sha256": terminal_artifact["sha256"],
                    "artifact": _pointer(terminal_artifact),
                },
                "failure": None,
            }
        else:
            domain = "infrastructure" if "infrastructure" in lifecycle_outcome else "runtime"
            failure = {
                "domain": domain,
                "code": lifecycle_outcome.replace("-", "_"),
                "message": "frozen attempt did not reach an eligible completed terminal state",
            }
            terminal = {"status": "inconclusive", "answer": None, "failure": failure}
            if domain == "infrastructure":
                infrastructure = {"status": "inconclusive", "failure": failure}

        payload: dict[str, object] = {
            "schema_version": "agent.evaluator.evidence-bundle/v2",
            "bundle_id": f"reference-{attempt_id[:32]}",
            "producer": {"producer_id": PRODUCER_ID, "producer_version": PRODUCER_VERSION},
            "run": {"run_id": f"experiment-{experiment_id[:48]}", "attempt_id": attempt_id},
            "profile": {
                "profile_id": profile_id,
                "profile_version": profile_version,
                "sha256": _canonical_identity(authority),
            },
            "case": {
                "case_id": case.case_id,
                "case_version": case.case_version,
                "case_set": case.case_set,
                "sha256": _canonical_identity(case),
            },
            "fixtures": tuple(
                fixture.model_dump(mode="json", exclude_none=False)
                for fixture in case.fixture_references
            ),
            "tool_actions": tuple(actions),
            "artifacts": tuple(artifacts),
            "terminal": terminal,
            "infrastructure": infrastructure,
        }
        payload["bundle_sha256"] = sha256_bytes(canonical_json_bytes(payload))
        return payload

    @staticmethod
    def _write_artifacts(
        staging: Path,
        root: Path,
        process: ProcessReceipt,
        lifecycle_outcome: str,
    ) -> None:
        if process.termination == "exited" and process.exit_code is not None:
            evidence = staging / "evidence"
            evidence.mkdir()
            (evidence / "test-results.json").write_bytes(
                (root / "tests/test-results.json").read_bytes()
            )
            (evidence / "test-stderr.txt").write_bytes((root / "tests/stderr.txt").read_bytes())
            if lifecycle_outcome in {"completed", "behavioral-failure"}:
                (evidence / "terminal.json").write_bytes((root / "terminal.json").read_bytes())
        elif lifecycle_outcome in {"completed", "behavioral-failure"}:
            raise AdapterError(
                "eligible completed lifecycle requires a complete test process receipt"
            )
