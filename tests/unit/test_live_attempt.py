from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from cernora_reference_workflow import live_attempt
from cernora_reference_workflow.runner import _AttemptRequest


def _request(tmp_path: Path, *, predecessor: str | None = None) -> _AttemptRequest:
    destination = tmp_path / "execution" / "attempts" / ("c" * 64) / "0002"
    destination.parent.mkdir(parents=True)
    return cast(
        _AttemptRequest,
        SimpleNamespace(
            execution_root=tmp_path / "execution",
            destination=destination,
            specification=SimpleNamespace(experiment_id="d" * 64),
            trial=SimpleNamespace(trial_id="c" * 64),
            active_record=SimpleNamespace(
                execution_id="e" * 64,
                ordinal=2,
                predecessor_attempt_id=predecessor,
            ),
        ),
    )


def test_adapter_passes_frozen_attempt_binding_to_single_attempt_tracer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "scripts" / "run_tracer.py"
    script.parent.mkdir()
    script.write_text("# fixture\n", encoding="utf-8")
    request = _request(tmp_path, predecessor="f" * 64)
    observed: list[tuple[str, ...]] = []
    observed_options: list[dict[str, object]] = []

    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append(command)
        observed_options.append(kwargs)
        request.destination.mkdir()
        return subprocess.CompletedProcess(command, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr(live_attempt, "_tracer_script", lambda: script)
    monkeypatch.setattr(subprocess, "run", fake_run)

    live_attempt.execute_qualified_live_attempt(request)

    assert len(observed) == 1
    command = observed[0]
    assert "--single-attempt" in command
    assert command[command.index("--ordinal") + 1] == "2"
    assert command[command.index("--destination") + 1] == str(request.destination)
    assert command[command.index("--predecessor-attempt-id") + 1] == "f" * 64
    assert observed_options == [
        {
            "cwd": script.parents[1],
            "check": False,
            "capture_output": True,
            "text": True,
            "start_new_session": True,
        }
    ]


def test_tracer_help_preserves_legacy_flags_and_adds_single_attempt_mode() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        (sys.executable, str(root / "scripts" / "run_tracer.py"), "--help"),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    for option in (
        "--spec",
        "--job-name",
        "--operator-interrupt",
        "--single-attempt",
        "--ordinal",
        "--predecessor-attempt-id",
        "--destination",
    ):
        assert option in result.stdout
