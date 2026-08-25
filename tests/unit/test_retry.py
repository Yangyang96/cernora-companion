from __future__ import annotations

from pathlib import Path

from cernora_reference_workflow.lifecycle import TerminalRecord
from cernora_reference_workflow.retry import AttemptResult, run_with_frozen_retry

A = "a" * 64
B = "b" * 64


def _terminal(
    attempt_id: str,
    state: str,
    predecessor_attempt_id: str | None,
) -> TerminalRecord:
    return TerminalRecord.model_validate(
        {
            "schema_version": "cernora.reference.terminal/v1",
            "attempt_id": attempt_id,
            "state": state,
            "reason": "deterministic-test",
            "retry_eligible": state
            in {"infrastructure-start-failure", "transient-provider-pre-terminal"},
            "predecessor_attempt_id": predecessor_attempt_id,
        }
    )


def test_eligible_failure_gets_one_retry_after_exactly_ten_seconds(tmp_path: Path) -> None:
    calls: list[tuple[int, str | None]] = []
    delays: list[float] = []

    def execute(ordinal: int, predecessor: str | None) -> AttemptResult[str]:
        calls.append((ordinal, predecessor))
        if ordinal == 1:
            terminal = _terminal(A, "infrastructure-start-failure", None)
        else:
            terminal = _terminal(B, "completed", A)
        return AttemptResult(terminal, f"trial-{ordinal}", tmp_path / str(ordinal), "value")

    attempts = run_with_frozen_retry(execute, sleeper=delays.append)
    assert calls == [(1, None), (2, A)]
    assert delays == [10]
    assert tuple(item.terminal.attempt_id for item in attempts) == (A, B)


def test_ineligible_failure_is_never_retried(tmp_path: Path) -> None:
    calls = 0

    def execute(ordinal: int, predecessor: str | None) -> AttemptResult[None]:
        nonlocal calls
        calls += 1
        return AttemptResult(
            _terminal(A, "timed-out", predecessor),
            "trial-1",
            tmp_path / str(ordinal),
            None,
        )

    attempts = run_with_frozen_retry(
        execute, sleeper=lambda _: (_ for _ in ()).throw(AssertionError)
    )
    assert len(attempts) == 1
    assert calls == 1


def test_second_eligible_failure_is_recorded_without_a_third_attempt(tmp_path: Path) -> None:
    calls = 0

    def execute(ordinal: int, predecessor: str | None) -> AttemptResult[None]:
        nonlocal calls
        calls += 1
        attempt_id = A if ordinal == 1 else B
        return AttemptResult(
            _terminal(attempt_id, "transient-provider-pre-terminal", predecessor),
            f"trial-{ordinal}",
            tmp_path / str(ordinal),
            None,
        )

    attempts = run_with_frozen_retry(execute, sleeper=lambda _: None)
    assert len(attempts) == 2
    assert calls == 2
