from __future__ import annotations

import json
import signal
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from cernora_reference_workflow import cli
from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.run_plan import materialize_run_plan
from tests.unit.test_run_plan import valid_payload


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
