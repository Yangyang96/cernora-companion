from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from cernora_reference_workflow.controlled_experiment_spec import (
    materialize_controlled_experiment_spec,
)
from cernora_reference_workflow.controlled_runtime import (
    MAX_CAPTURE_BYTES,
    observe_runtime_authority,
    run_subprocess_until,
)
from tests.unit.test_controlled_experiment_spec import valid_payload


def test_runtime_observation_recomputes_exact_experiment_authority() -> None:
    spec = materialize_controlled_experiment_spec(valid_payload())
    observation = observe_runtime_authority(spec)
    observation.verify(spec)
    changed = observation.model_copy(update={"prompt_sha256": "0" * 64})
    with pytest.raises(ValueError, match="does not equal"):
        changed.verify(spec)


def test_subprocess_is_killed_during_active_work_at_global_deadline(tmp_path: Path) -> None:
    started = time.monotonic()
    result = run_subprocess_until(
        (sys.executable, "-c", "import time; time.sleep(5)"),
        cwd=tmp_path,
        environment={},
        deadline_monotonic=started + 0.1,
        timeout_seconds=10,
    )

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert result.finished_monotonic - started < 2


def test_subprocess_capture_is_bounded_and_receipt_is_path_free(tmp_path: Path) -> None:
    result = run_subprocess_until(
        (
            sys.executable,
            "-c",
            f"import sys; sys.stdout.buffer.write(b'x' * {MAX_CAPTURE_BYTES + 4096})",
            str(tmp_path / "secret-proxy-example-18080"),
        ),
        cwd=tmp_path,
        environment={"https_proxy": "http://proxy.example:18080"},
        deadline_monotonic=time.monotonic() + 5,
        timeout_seconds=5,
    )

    assert result.status == "output_limit"
    assert result.stdout == result.stderr == b""
    portable = repr(result)
    assert str(tmp_path) not in portable
    assert "18080" not in portable


def test_active_disk_drop_kills_process_group_as_resumable_safe_stop(tmp_path: Path) -> None:
    probes = iter((20 * 1024**3, 7 * 1024**3))
    result = run_subprocess_until(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        environment={},
        deadline_monotonic=time.monotonic() + 10,
        timeout_seconds=10,
        disk_free=lambda: next(probes, 7 * 1024**3),
        safe_stop_free_bytes=8 * 1024**3,
    )

    assert result.status == "safe_stopped"
    assert result.exit_code is None


def test_deadline_kills_descendant_process_group(tmp_path: Path) -> None:
    child_pid = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)"
    )
    result = run_subprocess_until(
        (sys.executable, "-c", script, str(child_pid)),
        cwd=tmp_path,
        environment={},
        deadline_monotonic=time.monotonic() + 0.25,
        timeout_seconds=10,
    )

    assert result.status == "timed_out"
    pid = int(child_pid.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, signal.SIGCONT)
