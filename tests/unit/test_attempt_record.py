from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.attempt_record import (
    publish_preterminal_attempt,
    verify_preterminal_attempt,
)
from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.lifecycle import materialize_preterminal_record


def test_preterminal_attempt_record_is_closed_atomic_and_no_replace(tmp_path: Path) -> None:
    terminal = materialize_preterminal_record(
        experiment_id="a" * 64,
        source_trial_id="trial-1",
        state="infrastructure-start-failure",
        predecessor_attempt_id=None,
    )
    destination = tmp_path / "attempt"
    manifest = publish_preterminal_attempt(
        destination=destination,
        experiment_id="a" * 64,
        terminal=terminal,
        source_trial_id="trial-1",
    )
    assert verify_preterminal_attempt(destination) == manifest
    with pytest.raises(ContractError, match="must not already exist"):
        publish_preterminal_attempt(
            destination=destination,
            experiment_id="a" * 64,
            terminal=terminal,
            source_trial_id="trial-1",
        )


def test_preterminal_attempt_record_rejects_mutation(tmp_path: Path) -> None:
    terminal = materialize_preterminal_record(
        experiment_id="a" * 64,
        source_trial_id="trial-1",
        state="runtime-pre-terminal-failure",
        predecessor_attempt_id=None,
    )
    destination = tmp_path / "attempt"
    publish_preterminal_attempt(
        destination=destination,
        experiment_id="a" * 64,
        terminal=terminal,
        source_trial_id="trial-1",
    )
    (destination / "terminal.json").write_bytes(b"{}")
    with pytest.raises((ContractError, ValueError)):
        verify_preterminal_attempt(destination)
