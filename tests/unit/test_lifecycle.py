from __future__ import annotations

import pytest

from cernora_reference_workflow.lifecycle import (
    TerminalRecord,
    materialize_preterminal_record,
    select_terminal_attempt,
)

A = "a" * 64
B = "b" * 64


def record(
    attempt_id: str,
    state: str,
    *,
    predecessor: str | None = None,
    eligible: bool = False,
) -> TerminalRecord:
    return TerminalRecord.model_validate(
        {
            "schema_version": "cernora.reference.terminal/v1",
            "attempt_id": attempt_id,
            "state": state,
            "reason": "deterministic fixture",
            "retry_eligible": eligible,
            "predecessor_attempt_id": predecessor,
        }
    )


def test_one_eligible_retry_is_selected_without_hiding_first_attempt() -> None:
    first = record(A, "infrastructure-start-failure", eligible=True)
    second = record(B, "completed", predecessor=A)
    assert select_terminal_attempt((first, second)) == second


def test_behavioral_failure_cannot_be_retried() -> None:
    first = record(A, "behavioral-failure")
    second = record(B, "completed", predecessor=A)
    with pytest.raises(ValueError, match="authorized retry"):
        select_terminal_attempt((first, second))


def test_preterminal_attempt_identity_is_deterministic_and_predecessor_bound() -> None:
    first = materialize_preterminal_record(
        experiment_id="c" * 64,
        source_trial_id="trial-1",
        state="infrastructure-start-failure",
        predecessor_attempt_id=None,
    )
    repeated = materialize_preterminal_record(
        experiment_id="c" * 64,
        source_trial_id="trial-1",
        state="infrastructure-start-failure",
        predecessor_attempt_id=None,
    )
    second = materialize_preterminal_record(
        experiment_id="c" * 64,
        source_trial_id="trial-2",
        state="transient-provider-pre-terminal",
        predecessor_attempt_id=first.attempt_id,
    )
    assert first == repeated
    assert second.attempt_id != first.attempt_id
    assert second.predecessor_attempt_id == first.attempt_id
