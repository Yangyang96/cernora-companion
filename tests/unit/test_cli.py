from __future__ import annotations

import json
import signal
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from cernora_reference_workflow import cli
from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.comparison_input import ComparisonConfigurationError
from cernora_reference_workflow.controlled_study import materialize_study_intent
from cernora_reference_workflow.live_attempt import execute_qualified_live_attempt
from cernora_reference_workflow.run_plan import materialize_run_plan
from tests.unit.test_run_plan import valid_payload
from tests.unit.test_study_projection import study_payload_for_m4


def _plan_file(tmp_path: Path) -> tuple[Path, str]:
    plan = materialize_run_plan(valid_payload())
    path = tmp_path / "plan.json"
    path.write_bytes(plan.canonical_bytes())
    return path, plan.run_plan_id


def test_verify_prints_complete_deterministic_preflight(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path, plan_id = _plan_file(tmp_path)

    assert cli.main(("verify", str(plan_path))) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.out.encode() == canonical_json_bytes(payload)
    assert payload["run_plan_id"] == plan_id
    assert payload["planned_trial_count"] == 12
    assert [item["slot_index"] for item in payload["slots"]] == list(range(1, 13))
    assert len(payload["cells"]) == 4


def test_run_rejects_acceptance_mismatch_before_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path, _ = _plan_file(tmp_path)

    def must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("run initialized before exact acceptance")

    monkeypatch.setattr(cli, "run_repeat", must_not_run)
    assert (
        cli.main(
            (
                "run",
                str(plan_path),
                "--output",
                str(tmp_path / "execution"),
                "--accept-plan-id",
                "0" * 64,
            )
        )
        == 2
    )
    assert not (tmp_path / "execution").exists()


def test_rebuild_routes_to_offline_pack_rebuilder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    destination = tmp_path / "rebuilt"
    calls: list[tuple[Path, Path]] = []

    def fake_rebuild(source: Path, output: Path) -> object:
        calls.append((source, output))
        output.mkdir()
        return SimpleNamespace(execution_id="a" * 64, run_plan_id="b" * 64)

    monkeypatch.setattr(cli, "rebuild_execution_pack", fake_rebuild)
    assert cli.main(("rebuild", str(pack), "--output", str(destination))) == 0
    assert calls == [(pack, destination)]
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"


def test_study_cli_exposes_prepare_advance_and_offline_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    intent_payload, _ = study_payload_for_m4()
    intent = materialize_study_intent(intent_payload)
    intent_path = tmp_path / "intent.json"
    intent_path.write_bytes(canonical_json_bytes(intent.model_dump(mode="json")))
    study_root = tmp_path / "study"
    calls: list[tuple[str, Path]] = []

    def fake_prepare(_intent: object, output: Path) -> object:
        calls.append(("prepare", output))
        return SimpleNamespace(
            status="prepared",
            model_dump=lambda **_kwargs: {"status": "prepared", "study_id": "a" * 64},
        )

    monkeypatch.setattr(cli, "prepare_study", fake_prepare)
    assert cli.main(("study", "prepare", str(intent_path), "--output", str(study_root))) == 0
    assert calls == [("prepare", study_root)]
    assert json.loads(capsys.readouterr().out)["command"] == "study.prepare"

    study_root.mkdir()
    directive_path = tmp_path / "directive.json"
    directive_path.write_bytes(
        canonical_json_bytes(
            {
                "schema_version": "cernora.reference.advance-directive/v1",
                "action": "request-reveal",
            }
        )
    )

    def fake_advance(
        root: Path,
        directive: object,
        *,
        executor: object,
        should_stop: Callable[[], bool],
    ) -> object:
        calls.append(("advance", root))
        assert not should_stop()
        assert executor is execute_qualified_live_attempt
        return SimpleNamespace(
            status="awaiting-reveal",
            model_dump=lambda **_kwargs: {
                "status": "awaiting-reveal",
                "study_id": "a" * 64,
            },
        )

    monkeypatch.setattr(cli, "advance_study", fake_advance)
    assert (
        cli.main(
            (
                "study",
                "advance",
                str(study_root),
                "--directive",
                str(directive_path),
            )
        )
        == 0
    )
    assert calls[-1] == ("advance", study_root)
    assert json.loads(capsys.readouterr().out)["command"] == "study.advance"

    artifact = tmp_path / "artifact"
    artifact.mkdir()
    rebuilt = tmp_path / "rebuilt-study"

    def fake_study_rebuild(source: Path, output: Path) -> object:
        assert (source, output) == (artifact, rebuilt)
        return SimpleNamespace(
            artifact_id="b" * 64,
            kind="diagnostic-pack",
            terminal_status="paused",
        )

    monkeypatch.setattr(cli, "rebuild_study", fake_study_rebuild)
    assert cli.main(("study", "rebuild", str(artifact), "--output", str(rebuilt))) == 0
    assert json.loads(capsys.readouterr().out)["command"] == "study.rebuild"


def test_compare_routes_strict_inputs_and_reports_honest_conclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch = tmp_path / "batch"
    run_plan = tmp_path / "controlled-run-plan.json"
    comparison_plan = tmp_path / "comparison-plan.json"
    batch.mkdir()
    for path in (run_plan, comparison_plan):
        path.write_text("{}", encoding="utf-8")
    output = tmp_path / "comparison"
    calls: list[tuple[Path, Path, Path, Path]] = []

    def fake_compare(
        batch_root: Path,
        run_plan_path: Path,
        comparison_plan_path: Path,
        destination: Path,
    ) -> object:
        calls.append((batch_root, run_plan_path, comparison_plan_path, destination))
        destination.mkdir()
        return SimpleNamespace(
            comparison_id="comparison-" + "a" * 64,
            conclusion="regressed",
            summary_id="comparison-summary-" + "b" * 64,
        )

    monkeypatch.setattr(cli, "compare_batch_summary", fake_compare)
    assert (
        cli.main(
            (
                "compare",
                str(batch),
                "--run-plan",
                str(run_plan),
                "--plan",
                str(comparison_plan),
                "--output",
                str(output),
            )
        )
        == 0
    )
    assert calls == [(batch, run_plan, comparison_plan, output)]
    payload = json.loads(capsys.readouterr().out)
    assert payload["conclusion"] == "regressed"
    assert "winner" not in payload


def test_compare_reports_incompatible_authority_as_usage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def reject(*_args: object) -> object:
        raise ComparisonConfigurationError("comparison requires a controlled RunPlan v2")

    monkeypatch.setattr(cli, "compare_batch_summary", reject)
    assert (
        cli.main(
            (
                "compare",
                str(tmp_path / "batch"),
                "--run-plan",
                str(tmp_path / "legacy.json"),
                "--plan",
                str(tmp_path / "comparison.json"),
                "--output",
                str(tmp_path / "output"),
            )
        )
        == 2
    )
    assert "controlled RunPlan v2" in capsys.readouterr().err


def test_run_installs_sigint_stop_request_and_restores_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path, plan_id = _plan_file(tmp_path)
    previous = object()
    installed: list[object] = []

    monkeypatch.setattr(signal, "getsignal", lambda _signum: previous)

    def fake_signal(_signum: int, handler: object) -> object:
        installed.append(handler)
        return previous

    monkeypatch.setattr(signal, "signal", fake_signal)

    def fake_run(
        output: Path,
        plan: object,
        executor: object,
        *,
        should_stop: Callable[[], bool],
    ) -> object:
        assert output == tmp_path / "execution"
        assert not should_stop()
        handler = installed[-1]
        assert callable(handler)
        handler(signal.SIGINT, None)
        assert should_stop()
        return SimpleNamespace(
            status="stopped",
            state=SimpleNamespace(record=SimpleNamespace(execution_id="c" * 64)),
            pack_root=None,
        )

    monkeypatch.setattr(cli, "run_repeat", fake_run)
    assert (
        cli.main(
            (
                "run",
                str(plan_path),
                "--output",
                str(tmp_path / "execution"),
                "--accept-plan-id",
                plan_id,
            )
        )
        == 3
    )
    assert installed[-1] is previous
