from __future__ import annotations

import json
from pathlib import Path

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.export import publish_completed_export
from cernora_reference_workflow.lifecycle import materialize_preterminal_record
from cernora_reference_workflow.offline import evaluate_frozen_export
from cernora_reference_workflow.report_builder import (
    build_run_report,
    build_unavailable_run_report,
)
from cernora_reference_workflow.spec_builder import build_tiny_calculator_spec

from ..unit.test_export import materialize_staging

ROOT = Path(__file__).resolve().parents[2]


def test_three_strict_reloads_and_reports_are_byte_identical(tmp_path: Path) -> None:
    spec = build_tiny_calculator_spec(ROOT)
    staging = tmp_path / "staging"
    export = tmp_path / "completed-export"
    fields = materialize_staging(staging)
    fields["experiment_id"] = spec.experiment_id
    publish_completed_export(staging, export, manifest_fields=fields)

    reports = []
    evaluation_trees: list[dict[str, bytes]] = []
    for index in range(3):
        offline = evaluate_frozen_export(
            spec=spec,
            export_root=export,
            output_root=tmp_path / f"offline-{index}",
        )
        report = build_run_report(
            spec=spec,
            export_root=export,
            evaluation=offline,
            portable_spec_path="examples/tiny-calculator-v1.json",
            portable_export_path="frozen/success",
            portable_bundle_path="rebuilt/adapted/bundle.json",
            portable_evaluation_path="rebuilt/evaluated",
        )
        reports.append((report.canonical_bytes(), report.markdown().encode("utf-8")))
        evaluation_trees.append(
            {
                relative: path.read_bytes()
                for relative, path in sorted(
                    (path.relative_to(offline.evaluation_root).as_posix(), path)
                    for path in offline.evaluation_root.rglob("*")
                    if path.is_file()
                )
            }
        )

    assert reports[0] == reports[1] == reports[2]
    assert evaluation_trees[0] == evaluation_trees[1] == evaluation_trees[2]


def test_report_keeps_retry_eligible_predecessor_before_selected_export(tmp_path: Path) -> None:
    spec = build_tiny_calculator_spec(ROOT)
    staging = tmp_path / "staging"
    export = tmp_path / "completed-export"
    fields = materialize_staging(staging)
    fields["experiment_id"] = spec.experiment_id
    first = materialize_preterminal_record(
        experiment_id=spec.experiment_id,
        source_trial_id="trial-first",
        state="infrastructure-start-failure",
        predecessor_attempt_id=None,
    )
    terminal = fields["attempt_id"]
    assert isinstance(terminal, str)
    terminal_payload = json.loads((staging / "terminal.json").read_bytes())
    terminal_payload["predecessor_attempt_id"] = first.attempt_id
    (staging / "terminal.json").write_bytes(canonical_json_bytes(terminal_payload))
    publish_completed_export(staging, export, manifest_fields=fields)
    offline = evaluate_frozen_export(
        spec=spec,
        export_root=export,
        output_root=tmp_path / "offline",
    )
    report = build_run_report(
        spec=spec,
        export_root=export,
        evaluation=offline,
        portable_spec_path="examples/tiny-calculator-v1.json",
        portable_export_path="frozen/retry",
        portable_bundle_path="rebuilt/adapted/bundle.json",
        portable_evaluation_path="rebuilt/evaluated",
        prior_attempts=((first, "trial-first", "f" * 64),),
    )
    assert tuple(attempt.attempt_id for attempt in report.attempts) == (
        first.attempt_id,
        terminal,
    )
    assert report.attempts[1].retry_delay_seconds == 10
    assert report.attempts[0].artifact_manifest_sha256.status == "available"


def test_two_preterminal_failures_produce_complete_unavailable_report() -> None:
    spec = build_tiny_calculator_spec(ROOT)
    first = materialize_preterminal_record(
        experiment_id=spec.experiment_id,
        source_trial_id="trial-first",
        state="infrastructure-start-failure",
        predecessor_attempt_id=None,
    )
    second = materialize_preterminal_record(
        experiment_id=spec.experiment_id,
        source_trial_id="trial-second",
        state="runtime-pre-terminal-failure",
        predecessor_attempt_id=first.attempt_id,
    )
    report = build_unavailable_run_report(
        spec=spec,
        attempts=(
            (first, "trial-first", "e" * 64),
            (second, "trial-second", "f" * 64),
        ),
    )
    assert tuple(attempt.attempt_id for attempt in report.attempts) == (
        first.attempt_id,
        second.attempt_id,
    )
    assert report.selected_attempt_id == second.attempt_id
    assert report.evaluation.validity == "unavailable"
    assert report.evaluation.behavioral_decision == "inconclusive"
    assert all(
        attempt.artifact_manifest_sha256.status == "available" for attempt in report.attempts
    )


def test_report_uses_verifiable_trajectory_token_totals_when_emitted(tmp_path: Path) -> None:
    spec = build_tiny_calculator_spec(ROOT)
    staging = tmp_path / "staging"
    export = tmp_path / "completed-export"
    fields = materialize_staging(staging)
    fields["experiment_id"] = spec.experiment_id
    (staging / "runtime").mkdir()
    (staging / "runtime/trajectory.json").write_bytes(
        canonical_json_bytes(
            {
                "schema_version": "ATIF-v1.7",
                "final_metrics": {
                    "total_prompt_tokens": 120,
                    "total_completion_tokens": 30,
                    "extra": {"total_tokens": 150},
                },
                "steps": [{"message": "curl https://provider.invalid/diagnostic"}],
            }
        )
    )
    publish_completed_export(staging, export, manifest_fields=fields)
    offline = evaluate_frozen_export(
        spec=spec,
        export_root=export,
        output_root=tmp_path / "offline",
    )
    report = build_run_report(
        spec=spec,
        export_root=export,
        evaluation=offline,
        portable_spec_path="examples/tiny-calculator-v1.json",
        portable_export_path="frozen/tokens",
        portable_bundle_path="rebuilt/adapted/bundle.json",
        portable_evaluation_path="rebuilt/evaluated",
    )
    assert report.diagnostics.input_tokens.status == "available"
    assert report.diagnostics.input_tokens.value == 120
    assert report.diagnostics.output_tokens.status == "available"
    assert report.diagnostics.output_tokens.value == 30
    assert report.diagnostics.total_tokens.status == "available"
    assert report.diagnostics.total_tokens.value == 150
    assert report.diagnostics.network_capable_commands.status == "available"
    assert tuple(
        (item.command, item.occurrences)
        for item in report.diagnostics.network_capable_commands.observations
    ) == (("curl", 1),)
