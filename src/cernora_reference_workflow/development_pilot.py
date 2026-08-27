"""Deterministic offline development pilot and M4 CandidateFreeze generation."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from cernora import (
    BatchAttempt,
    BatchAttemptResources,
    BatchInput,
    BatchPlannedTrial,
    BatchSummaryPackage,
    BatchTrial,
    materialize_batch_input,
    reload_batch_summary_package,
    summarize_batch,
)

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_bytes,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_evaluation import (
    RepairResultRecord,
    materialize_repair_result,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    CanonicalAuthoritySource,
    materialize_authority_source,
)
from cernora_reference_workflow.controlled_profile import (
    PROFILE_ID,
    PROFILE_VERSION,
    evaluate_repair_result_package,
)
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
)
from cernora_reference_workflow.heldout_seal import HeldoutManifest
from cernora_reference_workflow.improvement_loop import (
    CandidateFreeze,
    derive_candidate_freeze,
)

DEVELOPMENT_CASE_IDS = (
    "dev-integer-ledger",
    "dev-interval-merge",
    "dev-query-codec",
)
PILOT_TIMEOUT_SECONDS = 10
PILOT_COMPANION_VERSION = "0.4.0-offline-pilot"
BASELINE_PROMPT_TEXT = "Repair the target so its frozen verifier passes."
_CANDIDATE_PROMPTS = {
    "interval_boundary_v1": (
        "When merging intervals, treat a shared endpoint as overlap and merge the touching "
        "intervals."
    ),
    "ledger_cents_sign_v1": (
        "When balancing decimal ledger entries, preserve exact signed cents without binary "
        "floating-point conversion."
    ),
    "query_repeated_escape_v1": (
        "When encoding query pairs, preserve repeated keys and percent-encode every key and value."
    ),
}
_OFFLINE_RUNNER = """\
import runpy
import sys

def deny_network(event, arguments):
    if event.startswith("socket."):
        raise RuntimeError("network disabled")

sys.addaudithook(deny_network)
verifier = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(verifier, run_name="__main__")
"""


def _identity(kind: str, facts: Mapping[str, object]) -> str:
    return canonical_content_id(
        {"kind": kind, "schema_version": "cernora.reference.development-pilot/v1", **facts},
        excluded=frozenset(),
    )


def _case_roots(visible_root: Path) -> tuple[Path, ...]:
    files = closed_regular_tree(visible_root)
    roots = tuple(sorted({relative.split("/", 1)[0] for relative in files}))
    if any("/" not in relative for relative in files):
        raise ContractError("visible corpus contains a root-level file")
    return tuple(visible_root / item for item in roots)


def load_development_tasks(visible_root: Path) -> tuple[ControlledTaskAuthority, ...]:
    """Load exactly the three checked-in visible development authorities."""

    selected: list[ControlledTaskAuthority] = []
    for case_root in _case_roots(visible_root):
        case_bytes = read_regular_file_bytes(case_root / "case.json")
        manifest = load_json_bytes(case_bytes)
        if not isinstance(manifest, dict) or manifest.get("split_id") != "development":
            continue
        selected.append(load_visible_task(case_root))
    tasks = tuple(sorted(selected, key=lambda item: item.case.case_id))
    case_ids = tuple(item.case.case_id for item in tasks)
    if case_ids != DEVELOPMENT_CASE_IDS or len(set(case_ids)) != len(case_ids):
        raise ContractError("visible corpus does not contain the exact development Case set")
    if any(item.split_id != "development" for item in tasks):
        raise ContractError("development pilot contains another split")
    return tasks


def _tree_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    snapshot: dict[str, tuple[int, str]] = {}
    for relative, path in closed_regular_tree(root).items():
        payload = read_regular_file_bytes(path, maximum=None)
        snapshot[relative] = (len(payload), sha256_bytes(payload))
    return snapshot


def _protected_digest(
    snapshot: Mapping[str, tuple[int, str]], protected_paths: tuple[str, ...]
) -> str:
    return canonical_content_id(
        {
            "files": [
                {
                    "path": path,
                    "size_bytes": snapshot[path][0],
                    "sha256": snapshot[path][1],
                }
                for path in protected_paths
                if path in snapshot
            ],
            "required_paths": list(protected_paths),
        },
        excluded=frozenset(),
    )


def _minimal_offline_environment() -> dict[str, str]:
    return {
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "TZ": "UTC",
    }


def _write_task_workspace(task: ControlledTaskAuthority, workspace: Path) -> None:
    for item in (*task.workspace_files, *task.test_files):
        destination = workspace.joinpath(*item.path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(item.content())


def _execute_baseline(task: ControlledTaskAuthority) -> RepairResultRecord:
    with tempfile.TemporaryDirectory(prefix="cernora-m4-development-pilot-") as temporary:
        workspace = Path(temporary)
        _write_task_workspace(task, workspace)
        before = _tree_snapshot(workspace)
        before_protected = _protected_digest(before, task.protected_paths)
        command = (
            sys.executable,
            "-I",
            "-c",
            _OFFLINE_RUNNER,
            task.test_command[1],
            *task.test_command[2:],
        )
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                env=_minimal_offline_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=PILOT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ContractError("development verifier exceeded the deterministic timeout") from exc
        except OSError as exc:
            raise ContractError("development verifier could not execute") from exc
        after = _tree_snapshot(workspace)
        after_protected = _protected_digest(after, task.protected_paths)
    changed_paths = tuple(
        sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    )
    test_plan_sha256 = canonical_content_id(
        {
            "command": list(task.test_command),
            "environment": _minimal_offline_environment(),
            "isolated_interpreter": True,
            "network_policy": "python_audit_hook_deny_socket_v1",
            "timeout_seconds": PILOT_TIMEOUT_SECONDS,
        },
        excluded=frozenset(),
    )
    return materialize_repair_result(
        {
            "schema_version": "cernora.reference.repair-result/v1",
            "case_id": task.case.case_id,
            "result_record_version": "agent.evaluator.result-record/v1",
            "test_authority_sha256": task.authority_sha256,
            "test_plan_sha256": test_plan_sha256,
            "test_source_sha256": task.test_source_sha256,
            "termination": "exited",
            "exit_code": completed.returncode,
            "checks": [
                {
                    "check_id": "frozen-verifier",
                    "failure_code": task.failure_code,
                    "passed": completed.returncode == 0,
                }
            ],
            "allowed_paths": list(task.allowed_paths),
            "changed_paths": list(changed_paths),
            "protected_paths": list(task.protected_paths),
            "protected_path_receipt": {
                "before_sha256": before_protected,
                "after_sha256": after_protected,
                "unchanged": before_protected == after_protected,
            },
        }
    )


def _pilot_batch(
    tasks: tuple[ControlledTaskAuthority, ...],
    results: tuple[RepairResultRecord, ...],
    evaluation_root: Path,
) -> BatchInput:
    task_bindings = [
        {"authority_id": task.authority_id, "authority_sha256": task.authority_sha256}
        for task in tasks
    ]
    run_plan_id = _identity("run-plan", {"tasks": task_bindings})
    execution_id = _identity("execution", {"run_plan_id": run_plan_id})
    planned: list[BatchPlannedTrial] = []
    trials: list[BatchTrial] = []
    for index, (task, result) in enumerate(zip(tasks, results, strict=True), start=1):
        common = {
            "case_id": task.case.case_id,
            "configuration_id": "baseline",
            "repetition": 1,
            "run_plan_id": run_plan_id,
        }
        trial_slot_id = _identity("trial-slot", common)
        experiment_id = _identity("experiment", {**common, "task_authority_id": task.authority_id})
        trial_id = _identity("trial", {**common, "execution_id": execution_id})
        source_attempt_id = _identity(
            "source-attempt", {"result_id": result.result_id, "trial_id": trial_id}
        )
        attempt_id = _identity(
            "attempt", {"ordinal": 1, "source_attempt_id": source_attempt_id, "trial_id": trial_id}
        )
        evaluation = evaluate_repair_result_package(
            task=task,
            tasks=tasks,
            result=result,
            source_attempt_id=source_attempt_id,
            output=evaluation_root / f"evaluation-{index}",
        )
        planned.append(
            BatchPlannedTrial(
                slot_index=index,
                trial_slot_id=trial_slot_id,
                case_id=task.case.case_id,
                configuration_id="baseline",
                experiment_id=experiment_id,
                repetition=1,
            )
        )
        attempt = BatchAttempt(
            schema_version="agent.evaluator.batch-attempt/v1",
            attempt_id=attempt_id,
            source_attempt_id=source_attempt_id,
            trial_id=trial_id,
            ordinal=1,
            predecessor_attempt_id=None,
            source_manifest_sha256=_identity(
                "source-manifest",
                {"result_id": result.result_id, "task_authority_id": task.authority_id},
            ),
            retry_eligible=False,
            resources=BatchAttemptResources(),
            evaluation=evaluation,
            lifecycle=None,
        )
        trials.append(
            BatchTrial(
                schema_version="agent.evaluator.batch-trial/v1",
                run_plan_id=run_plan_id,
                execution_id=execution_id,
                trial_id=trial_id,
                slot_index=index,
                trial_slot_id=trial_slot_id,
                case_id=task.case.case_id,
                configuration_id="baseline",
                experiment_id=experiment_id,
                repetition=1,
                selected_attempt_id=attempt_id,
                attempts=(attempt,),
            )
        )
    return materialize_batch_input(
        {
            "schema_version": "agent.evaluator.batch-input/v1",
            "run_plan_id": run_plan_id,
            "execution_id": execution_id,
            "execution_status": "completed",
            "budget_status": "within_budget",
            "companion_version": PILOT_COMPANION_VERSION,
            "planned_trial_count": len(planned),
            "attempt_count": len(trials),
            "planned_trials": [item.model_dump(mode="json") for item in planned],
            "trials": [item.model_dump(mode="json") for item in trials],
        }
    )


def create_development_pilot(*, visible_root: Path, output: Path) -> BatchSummaryPackage:
    """Run the real visible baselines and publish one strict Core pilot package."""

    if output.exists() or output.is_symlink():
        raise ContractError("development pilot output already exists")
    tasks = load_development_tasks(visible_root)
    results = tuple(_execute_baseline(task) for task in tasks)
    if not any(result.failure_codes for result in results):
        raise ContractError("development baseline has no usable versioned failure")
    with tempfile.TemporaryDirectory(prefix="cernora-m4-pilot-evaluations-") as temporary:
        batch = _pilot_batch(tasks, results, Path(temporary))
        summary = summarize_batch(batch, output)
    reloaded = reload_batch_summary_package(output)
    if reloaded.batch_input != batch or reloaded.summary != summary:
        raise ContractError("published development pilot changed during strict reload")
    return reloaded


def _prompt_authorities(
    pilot: BatchSummaryPackage,
) -> tuple[CanonicalAuthoritySource, CanonicalAuthoritySource]:
    counts: dict[str, int] = {}
    for trial in pilot.batch_input.trials:
        evaluation = trial.attempts[-1].evaluation
        if evaluation is None:
            raise ContractError("development pilot Trial is not evaluated")
        raw = evaluation.file_payloads().get("source-import/artifacts/evidence/repair-result.json")
        if raw is None:
            raise ContractError("development pilot omits its repair receipt")
        payload = load_json_bytes(raw)
        if not isinstance(payload, dict):
            raise ContractError("development pilot repair receipt is invalid")
        result = RepairResultRecord.model_validate(payload)
        if raw != canonical_json_bytes(result.model_dump(mode="json")):
            raise ContractError("development pilot repair receipt is not canonical")
        if result.case_id != trial.case_id:
            raise ContractError("development pilot repair receipt binds another Case")
        for code in result.failure_codes:
            counts[code] = counts.get(code, 0) + 1
    if not counts:
        raise ContractError("development baseline has no usable versioned failure")
    selected_code, _ = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    try:
        candidate_text = _CANDIDATE_PROMPTS[selected_code]
    except KeyError as exc:
        raise ContractError("development failure has no fixed prompt treatment") from exc
    baseline = materialize_authority_source("m4-baseline-prompt-v1", {"text": BASELINE_PROMPT_TEXT})
    candidate = materialize_authority_source(
        "m4-candidate-prompt-v1",
        {
            "selected_failure": {
                "code": selected_code,
                "profile_id": PROFILE_ID,
                "profile_version": PROFILE_VERSION,
            },
            "text": candidate_text,
        },
    )
    return baseline, candidate


def _atomic_publish_file(content: bytes, output: Path) -> None:
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ContractError("CandidateFreeze parent must be a real directory")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.staging-", dir=output.parent
    )
    temporary = Path(temporary_name)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, output, follow_symlinks=False)
        except FileExistsError as exc:
            raise ContractError("CandidateFreeze output already exists") from exc
        published = True
    except OSError as exc:
        raise ContractError("CandidateFreeze publication failed") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            if not published:
                raise


def create_candidate_freeze(
    *,
    visible_root: Path,
    public_manifest: Path,
    pilot_output: Path,
    freeze_output: Path,
) -> tuple[BatchSummaryPackage, CandidateFreeze]:
    """Publish the strict pilot, then derive and publish its immutable CandidateFreeze."""

    if freeze_output.exists() or freeze_output.is_symlink():
        raise ContractError("CandidateFreeze output already exists")
    manifest = HeldoutManifest.from_file(public_manifest)
    pilot = create_development_pilot(visible_root=visible_root, output=pilot_output)
    baseline, candidate = _prompt_authorities(pilot)
    freeze = derive_candidate_freeze(
        pilot_package=pilot,
        baseline_prompt_authority=baseline,
        candidate_prompt_authority=candidate,
        visible_corpus_root=visible_root,
        heldout_manifest=manifest,
    )
    canonical = freeze.canonical_bytes()
    _atomic_publish_file(canonical, freeze_output)
    reloaded = CandidateFreeze.from_file(freeze_output)
    if reloaded != freeze or read_regular_file_bytes(freeze_output) != canonical:
        raise ContractError("published CandidateFreeze changed during strict reload")
    return pilot, reloaded


__all__ = [
    "BASELINE_PROMPT_TEXT",
    "DEVELOPMENT_CASE_IDS",
    "PILOT_COMPANION_VERSION",
    "PILOT_TIMEOUT_SECONDS",
    "create_candidate_freeze",
    "create_development_pilot",
    "load_development_tasks",
]
