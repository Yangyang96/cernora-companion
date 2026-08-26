from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from cernora_reference_workflow.controlled_experiment_spec import (
    materialize_controlled_experiment_spec,
)
from cernora_reference_workflow.controlled_runtime import (
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
