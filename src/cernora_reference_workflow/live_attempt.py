"""Private adapter for the repository's one qualified Harbor/pi tracer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.runner import _AttemptRequest


def _tracer_script() -> Path:
    """Resolve the checked-in tracer; M1 intentionally has no generic connector SDK."""

    candidate = Path(__file__).resolve().parents[2] / "scripts" / "run_tracer.py"
    if not candidate.is_file() or candidate.is_symlink():
        raise ContractError(
            "the qualified Harbor/pi tracer is unavailable; run from the companion source tree"
        )
    return candidate


def _command(request: _AttemptRequest) -> tuple[str, ...]:
    spec_path = (
        request.execution_root / "specs" / f"{request.specification.experiment_id}.json"
    ).resolve()
    command = [
        sys.executable,
        str(_tracer_script()),
        "--spec",
        str(spec_path),
        "--job-name",
        f"p4-{request.active_record.execution_id[:12]}-{request.trial.trial_id[:12]}",
        "--single-attempt",
        "--ordinal",
        str(request.active_record.ordinal),
        "--destination",
        str(request.destination.resolve()),
    ]
    predecessor = request.active_record.predecessor_attempt_id
    if predecessor is not None:
        command.extend(("--predecessor-attempt-id", predecessor))
    return tuple(command)


def execute_qualified_live_attempt(request: _AttemptRequest) -> None:
    """Run exactly one attempt and require atomic publication before returning."""

    result = subprocess.run(
        _command(request),
        cwd=_tracer_script().parents[1],
        check=False,
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.strip().splitlines()
        detail = diagnostic[-1] if diagnostic else "qualified tracer failed without diagnostics"
        raise ContractError(f"qualified Harbor/pi attempt failed: {detail}")
    # The adapter only proves publication happened. The Runner's mandatory
    # strict reload owns contract verification and binding to ActiveAttempt.
    if not request.destination.is_dir() or request.destination.is_symlink():
        raise ContractError("qualified tracer returned without publishing its Attempt artifact")


__all__ = ["execute_qualified_live_attempt"]
