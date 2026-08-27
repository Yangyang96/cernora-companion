"""Real Profile and adapter pipeline for generic controlled repair receipts."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Literal

from cernora import (
    Artifact,
    AuthorityBoundImportPackageV2,
    BatchEvaluationPackage,
    CaseProfile,
    Evidence,
    EvidenceBundleV2,
    EvidenceReference,
    GatePolicy,
    ProfileAssessment,
    ProfileEvaluationContext,
    Score,
    ScoreObservation,
    ScorerPolicy,
    ToolAction,
    embed_evaluation_package,
    evaluate_imported_case,
    external_producer_identity,
    import_evidence_bundle_v2,
    read_imported_evaluation,
)

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    load_json_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_evaluation import RepairResultRecord
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.publication import atomic_publish_directory

PROFILE_ID = "cernora-controlled-repair-v1"
PROFILE_VERSION = "1.0.0"
PROJECTION_VERSION = "cernora.controlled-repair-projection/v1"
SCORER_VERSION = "cernora.controlled-repair-scorer/v1"
GATE_VERSION = "cernora.controlled-repair-gate/v1"
REQUIRED_OBSERVATIONS = (
    "authorized_paths_only_v1",
    "protected_paths_unchanged_v1",
    "repair_success_v1",
)


def build_controlled_profile_authority(
    tasks: tuple[ControlledTaskAuthority, ...],
) -> CaseProfile:
    ordered = tuple(sorted(tasks, key=lambda item: item.case.case_id))
    if not ordered or len({item.case.case_id for item in ordered}) != len(ordered):
        raise ContractError("controlled Profile tasks must be non-empty and unique")
    return CaseProfile(
        schema_version="agent.evaluator.case-profile/v1",
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
        description="Offline assessment of authority-bound controlled Python repair receipts.",
        cases=tuple(item.case for item in ordered),
        scorer_policy=ScorerPolicy(
            policy_version=SCORER_VERSION,
            required_observations=REQUIRED_OBSERVATIONS,
        ),
        gate_policy=GatePolicy(
            policy_version=GATE_VERSION,
            required_score_ids=("controlled-repair-score",),
            invalid_result="inconclusive",
        ),
    )


class ControlledRepairProfile:
    def __init__(self, tasks: tuple[ControlledTaskAuthority, ...]) -> None:
        self._tasks = {item.case.case_id: item for item in tasks}
        self._authority = build_controlled_profile_authority(tasks)

    @property
    def authority(self) -> CaseProfile:
        return self._authority

    @property
    def projection_version(self) -> str:
        return PROJECTION_VERSION

    def validate_import(self, package: AuthorityBoundImportPackageV2) -> None:
        if package.profile != self._authority:
            raise ValueError("import package does not bind the controlled Profile")
        task = self._tasks.get(package.case.case_id)
        if task is None or package.case != task.case:
            raise ValueError("import package does not bind a controlled task Case")
        bundle = package.content.bundle
        if bundle.producer.producer_id != "cernora-reference-workflow" or (
            bundle.producer.producer_version != "m4"
        ):
            raise ValueError("controlled bundle producer identity mismatch")
        expected_fixtures = tuple(
            item.model_dump(mode="json") for item in task.case.fixture_references
        )
        if tuple(item.model_dump(mode="json") for item in bundle.fixtures) != expected_fixtures:
            raise ValueError("controlled bundle fixtures do not match task authority")

    def _result(
        self, package: AuthorityBoundImportPackageV2
    ) -> tuple[RepairResultRecord, EvidenceReference]:
        bundle = package.content.bundle
        if bundle.terminal.status != "completed" or len(bundle.tool_actions) != 1:
            raise ValueError("controlled evaluation requires one completed Test Runner action")
        action = bundle.tool_actions[0]
        task = self._tasks[package.case.case_id]
        if (
            action.tool != "run_controlled_repair_tests"
            or action.argv != task.test_command
            or action.result.stdout_artifact.artifact_id != "repair-result"
            or action.result.stderr_artifact.artifact_id != "test-stderr"
            or not action.result.committed
            or not action.result.delivered
        ):
            raise ValueError("controlled Test Runner action contradicts task authority")
        raw = package.content.artifact_bytes["repair-result"]
        payload = load_json_bytes(raw)
        if not isinstance(payload, dict):
            raise ValueError("controlled repair result is not one JSON object")
        result = RepairResultRecord.model_validate(payload)
        if raw != canonical_json_bytes(result.model_dump(mode="json")):
            raise ValueError("controlled repair result is not canonical JSON")
        if (
            result.case_id != task.case.case_id
            or result.test_source_sha256 != task.test_source_sha256
            or result.allowed_paths != task.allowed_paths
            or result.protected_paths != task.protected_paths
        ):
            raise ValueError("controlled repair result does not bind task authority")
        artifact = next(item for item in bundle.artifacts if item.artifact_id == "repair-result")
        return result, EvidenceReference(
            evidence_id="placeholder",
            locator="artifacts/evidence/repair-result.json",
            sha256=artifact.sha256,
        )

    def assess(
        self,
        package: AuthorityBoundImportPackageV2,
        context: ProfileEvaluationContext,
    ) -> ProfileAssessment:
        self.validate_import(package)
        result, raw_reference = self._result(package)
        reference = raw_reference.model_copy(update={"evidence_id": context.evidence_id})
        records = result.core_result_records(reference)
        indexed = {item.id: item for item in records}
        observations: list[ScoreObservation] = []
        for observation_id in REQUIRED_OBSERVATIONS:
            record = indexed[observation_id]
            if record.validity == "valid":
                applicability: Literal["observed", "invalid"] = "observed"
                if type(record.value) is not bool:
                    raise ValueError("required controlled Profile record is not boolean")
                value: bool | None = record.value
                reason = None
            else:
                applicability = "invalid"
                value = None
                reason = record.failure_reason
            observations.append(
                ScoreObservation(
                    observation_id=observation_id,
                    applicability=applicability,
                    value=value,
                    reason=reason,
                    evidence_references=record.evidence_refs,
                )
            )
        score = Score(
            schema_version="agent.evaluator.score/v1",
            score_id=context.score_id,
            evidence_id=context.evidence_id,
            scorer_version=SCORER_VERSION,
            observations=tuple(observations),
        )
        bundle = package.content.bundle
        evidence = Evidence(
            schema_version="agent.evaluator.evidence/v1",
            evidence_id=context.evidence_id,
            evaluation_id=context.evaluation_id,
            profile_id=PROFILE_ID,
            case_id=package.case.case_id,
            run_id=bundle.run.run_id,
            producer=external_producer_identity(
                bundle.producer.producer_id, bundle.producer.producer_version
            ),
            process=None,
            tool_actions=tuple(
                ToolAction(
                    invocation_id=item.invocation_id,
                    tool=item.tool,
                    argv=item.argv,
                    exit_code=item.result.exit_code,
                    timed_out=item.result.status == "timed_out",
                    response_sha256=item.result.stdout_artifact.sha256,
                    committed=item.result.committed,
                    delivered=item.result.delivered,
                )
                for item in bundle.tool_actions
            ),
            artifacts=tuple(
                Artifact(
                    artifact_id=item.artifact_id,
                    path=item.path,
                    sha256=item.sha256,
                    media_type=item.media_type,
                )
                for item in bundle.artifacts
            ),
            answer=None,
            failures=(),
            metadata={
                "result_id": result.result_id,
                "task_authority_id": self._tasks[package.case.case_id].authority_id,
            },
        )
        return ProfileAssessment(
            evidence=evidence,
            score=score,
            required_observations=REQUIRED_OBSERVATIONS,
            result_records=records,
        )


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


def _bundle(
    task: ControlledTaskAuthority,
    profile: ControlledRepairProfile,
    result: RepairResultRecord,
    source_attempt_id: str,
) -> tuple[EvidenceBundleV2, dict[str, bytes]]:
    result_bytes = canonical_json_bytes(result.model_dump(mode="json"))
    stderr = b""
    terminal = b"controlled repair evaluation completed"
    result_artifact = _artifact(
        "repair-result", "evidence/repair-result.json", result_bytes, "application/json"
    )
    stderr_artifact = _artifact(
        "test-stderr", "evidence/test-stderr.txt", stderr, "text/plain; charset=utf-8"
    )
    terminal_artifact = _artifact(
        "terminal-record", "evidence/terminal.txt", terminal, "text/plain; charset=utf-8"
    )
    action: dict[str, object] = {
        "sequence": 0,
        "invocation_id": "controlled-test-runner-1",
        "tool": "run_controlled_repair_tests",
        "argv": task.test_command,
        "result": {
            "status": "completed" if result.exit_code == 0 else "failed",
            "exit_code": result.exit_code,
            "committed": True,
            "delivered": True,
            "stdout_artifact": _pointer(result_artifact),
            "stderr_artifact": _pointer(stderr_artifact),
        },
        "previous_receipt_sha256": None,
    }
    action["receipt_sha256"] = sha256_bytes(canonical_json_bytes(action))
    profile_sha = sha256_bytes(
        canonical_json_bytes(profile.authority.model_dump(mode="json", exclude_none=False))
    )
    payload: dict[str, object] = {
        "schema_version": "agent.evaluator.evidence-bundle/v2",
        "bundle_id": f"controlled-{source_attempt_id[:32]}",
        "producer": {"producer_id": "cernora-reference-workflow", "producer_version": "m4"},
        "run": {"run_id": f"controlled-{source_attempt_id[:48]}", "attempt_id": source_attempt_id},
        "profile": {
            "profile_id": PROFILE_ID,
            "profile_version": PROFILE_VERSION,
            "sha256": profile_sha,
        },
        "case": {
            "case_id": task.case.case_id,
            "case_version": task.case.case_version,
            "case_set": task.case.case_set,
            "sha256": task.case_sha256,
        },
        "fixtures": tuple(item.model_dump(mode="json") for item in task.case.fixture_references),
        "tool_actions": (action,),
        "artifacts": (result_artifact, stderr_artifact, terminal_artifact),
        "terminal": {
            "status": "completed",
            "answer": {
                "content": terminal.decode(),
                "sha256": sha256_bytes(terminal),
                "artifact": _pointer(terminal_artifact),
            },
            "failure": None,
        },
        "infrastructure": {"status": "valid", "failure": None},
    }
    payload["bundle_sha256"] = sha256_bytes(canonical_json_bytes(payload))
    return EvidenceBundleV2.model_validate(payload), {
        "evidence/repair-result.json": result_bytes,
        "evidence/test-stderr.txt": stderr,
        "evidence/terminal.txt": terminal,
    }


def evaluate_repair_result_package(
    *,
    task: ControlledTaskAuthority,
    tasks: tuple[ControlledTaskAuthority, ...],
    result: RepairResultRecord,
    source_attempt_id: str,
    output: Path,
) -> BatchEvaluationPackage:
    """Publish, import, evaluate, and strict-reload one real Core package."""

    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ContractError("controlled evaluation output must be a new child of a real parent")
    if not result.evaluation_valid:
        raise ContractError("non-evaluated repair result cannot become an Evaluation Package")
    profile = ControlledRepairProfile(tasks)
    bundle, artifacts = _bundle(task, profile, result, source_attempt_id)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    published = False
    try:
        source = staging / "source"
        source.mkdir()
        (source / "bundle.json").write_bytes(
            canonical_json_bytes(bundle.model_dump(mode="json", exclude_none=False))
        )
        for relative, content in artifacts.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        imported = staging / "imported"
        import_evidence_bundle_v2(
            profile=profile,
            bundle_path=source / "bundle.json",
            output=imported,
        )
        evaluated = staging / "evaluated"
        receipt = evaluate_imported_case(profile, imported, evaluated)
        if read_imported_evaluation(evaluated, profile) != receipt:
            raise ContractError("controlled Evaluation Package strict reload mismatch")
        atomic_publish_directory(staging, output)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)
    return embed_evaluation_package(output / "evaluated")


__all__ = [
    "GATE_VERSION",
    "PROFILE_ID",
    "PROFILE_VERSION",
    "PROJECTION_VERSION",
    "REQUIRED_OBSERVATIONS",
    "SCORER_VERSION",
    "ControlledRepairProfile",
    "build_controlled_profile_authority",
    "evaluate_repair_result_package",
]
