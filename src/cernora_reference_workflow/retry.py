"""Frozen one-retry orchestration shared by the live tracer and deterministic tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep as system_sleep

from cernora_reference_workflow.lifecycle import TerminalRecord, select_terminal_attempt

RETRY_DELAY_SECONDS = 10


@dataclass(frozen=True)
class AttemptResult[T]:
    terminal: TerminalRecord
    source_trial_id: str
    raw_attempt_root: Path
    value: T


def run_with_frozen_retry[T](
    execute: Callable[[int, str | None], AttemptResult[T]],
    *,
    sleeper: Callable[[float], None] = system_sleep,
) -> tuple[AttemptResult[T], ...]:
    """Run once, then exactly one fixed-delay retry only for an eligible terminal state."""

    first = execute(1, None)
    if not first.terminal.retry_eligible:
        select_terminal_attempt((first.terminal,))
        return (first,)
    sleeper(RETRY_DELAY_SECONDS)
    second = execute(2, first.terminal.attempt_id)
    select_terminal_attempt((first.terminal, second.terminal))
    return (first, second)


__all__ = ["RETRY_DELAY_SECONDS", "AttemptResult", "run_with_frozen_retry"]
