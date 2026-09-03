"""Build the portable run report from verified export and strict-reload bytes."""

from __future__ import annotations

import re
from pathlib import Path

from cernora_reference_workflow import adapter as adapter_module
from cernora_reference_workflow.common import (
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
    sha256_bytes,
    sha256_file,
    sha256_installed_code,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.export import verify_completed_export
from cernora_reference_workflow.lifecycle import TerminalRecord
from cernora_reference_workflow.offline import OfflineEvaluation
from cernora_reference_workflow.report import (
    RunReport,
    StrictReloadVerified,
    materialize_run_report,
)
from cernora_reference_workflow.test_runner import ResourceReceipt


def _tree_identity(root: Path) -> str:
    payload = {
        "files": [
            {"path": relative, "sha256": sha256_file(path)}
            for relative, path in closed_regular_tree(root).items()
        ]
    }
    return sha256_bytes(canonical_json_bytes(payload))


def _available(sha256: str) -> dict[str, object]:
    return {"status": "available", "sha256": sha256}


def _missing(reason: str) -> dict[str, object]:
    return {"status": "missing", "reason": reason}


def _token_diagnostics(export_root: Path) -> dict[str, dict[str, object]]:
    trajectory = export_root / "runtime/trajectory.json"
    missing = {
        "input_tokens": _missing("not-emitted"),
        "output_tokens": _missing("not-emitted"),
        "total_tokens": _missing("not-emitted"),
    }
    if not trajectory.is_file():
        return missing
    payload = load_json_file(trajectory)
    if not isinstance(payload, dict):
        return {name: _missing("not-verifiable") for name in missing}
    final_metrics = payload.get("final_metrics")
    if not isinstance(final_metrics, dict):
        return missing
    source_sha256 = sha256_file(trajectory)

    def available(value: object) -> dict[str, object] | None:
        if type(value) is not int or value < 0:
            return None
        return {
            "status": "available",
            "value": value,
            "unit": "tokens",
            "source_receipt_sha256": source_sha256,
        }

    input_value = final_metrics.get("total_prompt_tokens")
    output_value = final_metrics.get("total_completion_tokens")
    input_metric = available(input_value)
    output_metric = available(output_value)
    extra = final_metrics.get("extra")
    emitted_total = extra.get("total_tokens") if isinstance(extra, dict) else None
    total_metric = available(emitted_total)
    if (
        total_metric is None
        and type(input_value) is int
        and type(output_value) is int
        and input_value >= 0
        and output_value >= 0
    ):
        total_metric = available(input_value + output_value)
    return {
        "input_tokens": input_metric or missing["input_tokens"],
        "output_tokens": output_metric or missing["output_tokens"],
        "total_tokens": total_metric or missing["total_tokens"],
    }


def _network_command_diagnostics(export_root: Path) -> dict[str, object]:
    candidates = (
        export_root / "runtime/trajectory.json",
        export_root / "runtime/pi-events.jsonl",
    )
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        return _missing("not-emitted")
    data = source.read_bytes().lower()
    observations = [
        {"command": command.decode("ascii"), "occurrences": count}
        for command in (b"curl", b"wget")
        if (count := len(re.findall(rb"(?<![a-z0-9_-])" + command + rb"(?![a-z0-9_-])", data))) > 0
    ]
    return {
        "status": "available",
        "source_receipt_sha256": sha256_file(source),
        "observations": observations,
    }


def _components(spec: ExperimentSpec) -> dict[str, object]:
    return {
        "task": {
            "task_id": spec.task.task_id,
            "task_version": spec.task.task_version,
            "content_sha256": spec.task.content_sha256,
            "prompt_sha256": spec.task.prompt_sha256,
            "instruction_sha256": spec.task.instruction_sha256,
        },
        "container": {
            "image": spec.container.image,
            "build_base_image": spec.container.build_base_image,
            "platform": spec.container.platform,
        },
        "harness": spec.harness.model_dump(mode="json"),
        "runtime": {
            "name": spec.runtime.name,
            "version": spec.runtime.version,
            "configuration_sha256": spec.runtime.configuration_sha256,
        },
        "model": {
            "name": spec.runtime.model,
            "reasoning_effort": spec.runtime.reasoning_effort,
            "web_search": spec.network.web_search,
            "provider_egress": spec.network.provider_egress,
        },
        "cernora": spec.cernora.model_dump(mode="json"),
        "profile": spec.profile.model_dump(mode="json"),
        "adapter": {
            "adapter_id": "cernora-reference-adapter",
            "adapter_version": "1",
            "authority_sha256": sha256_installed_code(Path(adapter_module.__file__)),
        },
    }


def _unexported_attempts(
    attempts: tuple[tuple[TerminalRecord, str, str | None], ...],
) -> list[dict[str, object]]:
    return [
        {
            "ordinal": ordinal,
            "attempt_id": terminal.attempt_id,
            "predecessor_attempt_id": terminal.predecessor_attempt_id,
            "retry_delay_seconds": 10 if ordinal == 2 else None,
            "source_trial_id": source_trial_id,
            "lifecycle_outcome": terminal.state,
            "lifecycle_reason_code": terminal.reason,
            "retry_eligible": terminal.retry_eligible,
            "completed_export_sha256": _missing("attempt-not-exported"),
            "candidate_tree_sha256": _missing("attempt-not-exported"),
            "artifact_manifest_sha256": (
                _available(artifact_manifest_sha256)
                if artifact_manifest_sha256 is not None
                else _missing("attempt-not-exported")
            ),
        }
        for ordinal, (terminal, source_trial_id, artifact_manifest_sha256) in enumerate(
            attempts,
            start=1,
        )
    ]


def build_unavailable_run_report(
    *,
    spec: ExperimentSpec,
    attempts: tuple[tuple[TerminalRecord, str, str | None], ...],
) -> RunReport:
    """Report a selected pre-terminal attempt without inventing export or evaluation evidence."""

    if not attempts or len(attempts) > 2:
        raise ValueError("unavailable report requires one attempt or one eligible retry")
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.run-report/v1",
        "experiment_id": spec.experiment_id,
        "components": _components(spec),
        "attempts": _unexported_attempts(attempts),
        "selected_attempt_id": attempts[-1][0].attempt_id,
        "evaluation": {
            "validity": "unavailable",
            "behavioral_decision": "inconclusive",
            "gate_decision": "inconclusive",
            "strict_reload": {
                "status": "not-performed",
                "reason": "evaluation-not-performed",
            },
        },
        "test_runner": {
            "authority_id": spec.test_runner.authority_id,
            "authority_version": spec.test_runner.authority_version,
            "authority_sha256": spec.test_runner.authority_sha256,
            "test_plan_sha256": _missing("attempt-not-exported"),
            "test_results_sha256": _missing("attempt-not-exported"),
            "process_receipt_sha256": _missing("attempt-not-exported"),
            "resource_receipt_sha256": _missing("attempt-not-exported"),
        },
        "diagnostics": {
            "duration": _missing("not-collected"),
            "peak_memory": _missing("not-collected"),
            "cpu_time": _missing("not-collected"),
            "input_tokens": _missing("not-emitted"),
            "output_tokens": _missing("not-emitted"),
            "total_tokens": _missing("not-emitted"),
            "network_capable_commands": _missing("not-emitted"),
            "runtime_boundary_observation_sha256": _missing("not-emitted"),
        },
        "offline_rebuild": {
            "status": "missing",
            "reason": "evaluation-not-performed",
        },
    }
    return materialize_run_report(payload)


def build_run_report(
    *,
    spec: ExperimentSpec,
    export_root: Path,
    evaluation: OfflineEvaluation,
    portable_spec_path: str,
    portable_export_path: str,
    portable_bundle_path: str,
    portable_evaluation_path: str,
    prior_attempts: tuple[tuple[TerminalRecord, str, str | None], ...] = (),
) -> RunReport:
    if len(prior_attempts) > 1:
        raise ValueError("the frozen policy permits at most one prior retry-eligible attempt")
    manifest = verify_completed_export(export_root)
    terminal_payload = load_json_file(export_root / "terminal.json")
    resources_payload = load_json_file(export_root / "receipts/resources.json")
    bundle_payload = load_json_file(evaluation.bundle_path)
    if not all(
        isinstance(value, dict) for value in (terminal_payload, resources_payload, bundle_payload)
    ):
        raise ValueError("verified report inputs must contain JSON objects")
    terminal = TerminalRecord.model_validate(terminal_payload)
    resources = ResourceReceipt.model_validate(resources_payload)
    bundle_id = bundle_payload.get("bundle_id")
    bundle_sha256 = bundle_payload.get("bundle_sha256")
    if not isinstance(bundle_id, str) or not isinstance(bundle_sha256, str):
        raise ValueError("verified EvidenceBundle is missing its public identities")
    export_sha256 = _tree_identity(export_root)
    evaluation_sha256 = _tree_identity(evaluation.evaluation_root)
    manifest_sha256 = sha256_file(export_root / "manifest.json")
    bundle_file_sha256 = sha256_file(evaluation.bundle_path)
    gate_sha256 = sha256_file(evaluation.evaluation_root / "case-decision.json")
    evaluation_receipt_sha256 = sha256_file(evaluation.evaluation_root / "evaluation-receipt.json")
    receipt = evaluation.receipt
    strict_payload: dict[str, object] = {
        "status": "verified",
        "bundle_id": bundle_id,
        "evidence_bundle_sha256": bundle_sha256,
        "evaluation_input_sha256": receipt.evaluation_input_sha256,
        "evaluation_id": receipt.evaluation_id,
        "evidence_id": receipt.evidence_id,
        "score_id": receipt.score_id,
        "decision_id": receipt.decision_id,
        "gate_decision_sha256": gate_sha256,
        "evaluation_receipt_sha256": evaluation_receipt_sha256,
    }
    strict_payload["result_identity_sha256"] = StrictReloadVerified.compute_identity(strict_payload)
    resource_sha256 = sha256_file(export_root / "receipts/resources.json")
    token_diagnostics = _token_diagnostics(export_root)
    network_commands = _network_command_diagnostics(export_root)
    runtime_observation = export_root / "runtime/runtime-boundary-observation.json"
    duration: dict[str, object]
    if resources.duration_milliseconds is None:
        duration = _missing("not-collected")
    else:
        duration = {
            "status": "available",
            "value": resources.duration_milliseconds,
            "unit": "milliseconds",
            "source_receipt_sha256": resource_sha256,
        }
    behavioral = receipt.case_outcome
    validity = "valid" if behavioral in {"pass", "fail"} else "invalid"
    attempt_payloads: list[dict[str, object]] = []
    for ordinal, (prior, source_trial_id, artifact_manifest_sha256) in enumerate(
        prior_attempts,
        start=1,
    ):
        attempt_payloads.append(
            {
                "ordinal": ordinal,
                "attempt_id": prior.attempt_id,
                "predecessor_attempt_id": prior.predecessor_attempt_id,
                "retry_delay_seconds": None,
                "source_trial_id": source_trial_id,
                "lifecycle_outcome": prior.state,
                "lifecycle_reason_code": prior.reason,
                "retry_eligible": prior.retry_eligible,
                "completed_export_sha256": _missing("attempt-not-exported"),
                "candidate_tree_sha256": _missing("attempt-not-exported"),
                "artifact_manifest_sha256": (
                    _available(artifact_manifest_sha256)
                    if artifact_manifest_sha256 is not None
                    else _missing("attempt-not-exported")
                ),
            }
        )
    selected_ordinal = len(attempt_payloads) + 1
    attempt_payloads.append(
        {
            "ordinal": selected_ordinal,
            "attempt_id": terminal.attempt_id,
            "predecessor_attempt_id": terminal.predecessor_attempt_id,
            "retry_delay_seconds": 10 if selected_ordinal == 2 else None,
            "source_trial_id": manifest.source_trial_id,
            "lifecycle_outcome": terminal.state,
            "lifecycle_reason_code": terminal.reason,
            "retry_eligible": terminal.retry_eligible,
            "completed_export_sha256": _available(export_sha256),
            "candidate_tree_sha256": _available(manifest.post_candidate_tree_sha256),
            "artifact_manifest_sha256": _available(manifest_sha256),
        }
    )
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.run-report/v1",
        "experiment_id": spec.experiment_id,
        "components": _components(spec),
        "attempts": attempt_payloads,
        "selected_attempt_id": terminal.attempt_id,
        "evaluation": {
            "validity": validity,
            "behavioral_decision": behavioral,
            "gate_decision": behavioral,
            "strict_reload": strict_payload,
        },
        "test_runner": {
            "authority_id": manifest.test_authority_id,
            "authority_version": "1",
            "authority_sha256": manifest.test_authority_sha256,
            "test_plan_sha256": _available(manifest.test_plan_sha256),
            "test_results_sha256": _available(sha256_file(export_root / "tests/test-results.json")),
            "process_receipt_sha256": _available(
                sha256_file(export_root / "receipts/process.json")
            ),
            "resource_receipt_sha256": _available(resource_sha256),
        },
        "diagnostics": {
            "duration": duration,
            "peak_memory": _missing("not-collected"),
            "cpu_time": _missing("not-collected"),
            **token_diagnostics,
            "network_capable_commands": network_commands,
            "runtime_boundary_observation_sha256": (
                _available(sha256_file(runtime_observation))
                if runtime_observation.is_file()
                else _missing("not-emitted")
            ),
        },
        "offline_rebuild": {
            "status": "available",
            "experiment_spec": {
                "path": portable_spec_path,
                "sha256": sha256_bytes(spec.canonical_bytes()),
            },
            "completed_export": {
                "path": portable_export_path,
                "sha256": export_sha256,
            },
            "adapted_bundle": {
                "path": portable_bundle_path,
                "sha256": bundle_file_sha256,
            },
            "evaluation_output": {
                "path": portable_evaluation_path,
                "sha256": evaluation_sha256,
            },
            "commands": [
                {
                    "purpose": "adapt",
                    "working_directory": ".",
                    "argv": [
                        "uv",
                        "run",
                        "--frozen",
                        "python",
                        "scripts/evaluate_frozen.py",
                        "adapt",
                        "--spec",
                        portable_spec_path,
                        "--export",
                        portable_export_path,
                        "--output",
                        portable_bundle_path.rsplit("/", 1)[0],
                    ],
                },
                {
                    "purpose": "evaluate",
                    "working_directory": ".",
                    "argv": [
                        "uv",
                        "run",
                        "--frozen",
                        "python",
                        "scripts/evaluate_frozen.py",
                        "evaluate",
                        "--bundle",
                        portable_bundle_path,
                        "--output",
                        portable_evaluation_path,
                    ],
                },
                {
                    "purpose": "strict-reload",
                    "working_directory": ".",
                    "argv": [
                        "uv",
                        "run",
                        "--frozen",
                        "python",
                        "scripts/evaluate_frozen.py",
                        "reload",
                        "--output",
                        portable_evaluation_path,
                    ],
                },
            ],
        },
    }
    return materialize_run_report(payload)


__all__ = ["build_run_report", "build_unavailable_run_report"]
