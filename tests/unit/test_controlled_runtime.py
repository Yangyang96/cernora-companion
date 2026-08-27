from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
from pathlib import Path

import pytest

from cernora_reference_workflow.controlled_experiment_spec import (
    materialize_controlled_experiment_spec,
)
from cernora_reference_workflow.controlled_runtime import (
    MAX_CAPTURE_BYTES,
    _capture_identity,
    _cleanup_bound_capture,
    _read_bound_capture,
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


@pytest.mark.parametrize("marker_kind", ("directory", "file"))
def test_subprocess_capture_stays_outside_every_git_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker_kind: str,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    marker = worktree / ".git"
    if marker_kind == "directory":
        marker.mkdir()
    else:
        marker.write_text("gitdir: elsewhere\n", encoding="utf-8")
    private_capture_parent = tmp_path / "private-captures"
    private_capture_parent.mkdir()
    observed_parents: list[Path | None] = []
    real_mkdtemp = tempfile.mkdtemp

    def observed_mkdtemp(*, prefix: str, dir: Path | str | None = None) -> str:
        observed_parents.append(Path(dir).resolve() if dir is not None else None)
        return str(real_mkdtemp(prefix=prefix, dir=dir))

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(private_capture_parent))
    monkeypatch.setattr(tempfile, "mkdtemp", observed_mkdtemp)

    result = run_subprocess_until(
        (sys.executable, "-c", "print('captured outside worktree')"),
        cwd=worktree,
        environment={},
        deadline_monotonic=time.monotonic() + 5,
        timeout_seconds=5,
    )

    assert result.status == "exited"
    assert observed_parents == [private_capture_parent.resolve()]
    assert not tuple(worktree.glob("cernora-capture-*"))
    assert not tuple(private_capture_parent.glob("cernora-capture-*"))


@pytest.mark.parametrize("marker_kind", ("directory", "file"))
def test_subprocess_rejects_a_capture_parent_inside_any_git_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker_kind: str,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    marker = worktree / ".git"
    if marker_kind == "directory":
        marker.mkdir()
    else:
        marker.write_text("gitdir: elsewhere\n", encoding="utf-8")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(worktree))

    with pytest.raises(RuntimeError, match="outside every Git worktree"):
        run_subprocess_until(
            (sys.executable, "-c", "print('must not start')"),
            cwd=worktree,
            environment={},
            deadline_monotonic=time.monotonic() + 5,
            timeout_seconds=5,
        )

    assert not tuple(worktree.glob("cernora-capture-*"))


def test_capture_cleanup_preserves_a_replacement_directory(tmp_path: Path) -> None:
    capture = tmp_path / "cernora-capture-owned"
    capture.mkdir()
    (capture / "stdout.bin").write_bytes(b"")
    (capture / "stderr.bin").write_bytes(b"")
    directory_fd = os.open(
        capture,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    directory_identity = _capture_identity(os.fstat(directory_fd))
    file_identities = {
        name: _capture_identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
        for name in ("stdout.bin", "stderr.bin")
    }
    displaced = tmp_path / "displaced-owned-capture"
    capture.rename(displaced)
    capture.mkdir()
    sentinel = capture / "not-owned.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match="ownership became ambiguous"):
            _cleanup_bound_capture(
                capture,
                directory_fd,
                directory_identity,
                file_identities,
            )
    finally:
        os.close(directory_fd)

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert {path.name for path in displaced.iterdir()} == {"stdout.bin", "stderr.bin"}


def test_bound_capture_reports_growth_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = tmp_path / "capture"
    capture.mkdir()
    output = capture / "stdout.bin"
    output.write_bytes(b"x" * MAX_CAPTURE_BYTES)
    directory_fd = os.open(capture, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    expected = _capture_identity(output.stat(follow_symlinks=False))
    real_read = os.read
    appended = False

    def growing_read(descriptor: int, length: int) -> bytes:
        nonlocal appended
        chunk = real_read(descriptor, length)
        if chunk and not appended:
            with output.open("ab") as stream:
                stream.write(b"y")
            appended = True
        return chunk

    monkeypatch.setattr(os, "read", growing_read)
    try:
        data, overflow = _read_bound_capture(directory_fd, "stdout.bin", expected)
    finally:
        os.close(directory_fd)

    assert data == b""
    assert overflow is True


def test_bound_capture_rejects_same_size_rewrite_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = tmp_path / "capture"
    capture.mkdir()
    output = capture / "stdout.bin"
    output.write_bytes(b"x" * (128 * 1024))
    directory_fd = os.open(capture, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    expected = _capture_identity(output.stat(follow_symlinks=False))
    real_read = os.read
    rewritten = False

    def rewriting_read(descriptor: int, length: int) -> bytes:
        nonlocal rewritten
        chunk = real_read(descriptor, length)
        if chunk and not rewritten:
            output.write_bytes(b"z" * (128 * 1024))
            rewritten = True
        return chunk

    monkeypatch.setattr(os, "read", rewriting_read)
    try:
        with pytest.raises(RuntimeError, match="changed during stable read"):
            _read_bound_capture(directory_fd, "stdout.bin", expected)
    finally:
        os.close(directory_fd)


def test_mid_read_growth_classifies_the_whole_process_as_output_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def overflow(
        directory_fd: int,
        name: str,
        expected: tuple[int, int, int, int],
    ) -> tuple[bytes, bool]:
        del directory_fd, name, expected
        return b"", True

    monkeypatch.setattr(
        "cernora_reference_workflow.controlled_runtime._read_bound_capture",
        overflow,
    )
    result = run_subprocess_until(
        (sys.executable, "-c", "print('small output')"),
        cwd=tmp_path,
        environment={},
        deadline_monotonic=time.monotonic() + 5,
        timeout_seconds=5,
    )

    assert result.status == "output_limit"
    assert result.exit_code is None
    assert result.stdout == result.stderr == b""


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
