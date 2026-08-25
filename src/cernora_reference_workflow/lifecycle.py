"""Attempt lifecycle and the frozen retry-selection policy."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictStr, model_validator

from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.experiment_spec import Digest, StrictContract

TerminalState = Literal[
    "completed",
    "behavioral-failure",
    "timed-out",
    "interrupted",
    "infrastructure-start-failure",
    "transient-provider-pre-terminal",
    "runtime-pre-terminal-failure",
]


class TerminalRecord(StrictContract):
    schema_version: Literal["cernora.reference.terminal/v1"]
    attempt_id: Digest
    state: TerminalState
    reason: Annotated[StrictStr, Field(min_length=1)]
    retry_eligible: StrictBool
    predecessor_attempt_id: Digest | None

    @model_validator(mode="after")
    def validate_retry_semantics(self) -> TerminalRecord:
        eligible = self.state in {
            "infrastructure-start-failure",
            "transient-provider-pre-terminal",
        }
        if self.retry_eligible is not eligible:
            raise ValueError("retry_eligible contradicts terminal state")
        return self


def materialize_preterminal_record(
    *,
    experiment_id: str,
    source_trial_id: str,
    state: Literal[
        "infrastructure-start-failure",
        "transient-provider-pre-terminal",
        "runtime-pre-terminal-failure",
    ],
    predecessor_attempt_id: str | None,
) -> TerminalRecord:
    """Create a portable identity without copying raw exception text or host metadata."""

    reason = {
        "infrastructure-start-failure": "runtime-start-failure",
        "transient-provider-pre-terminal": "transient-provider-pre-terminal",
        "runtime-pre-terminal-failure": "runtime-pre-terminal-failure",
    }[state]
    attempt_id = sha256_bytes(
        canonical_json_bytes(
            {
                "experiment_id": experiment_id,
                "predecessor_attempt_id": predecessor_attempt_id,
                "source_trial_id": source_trial_id,
                "state": state,
            }
        )
    )
    return TerminalRecord(
        schema_version="cernora.reference.terminal/v1",
        attempt_id=attempt_id,
        state=state,
        reason=reason,
        retry_eligible=state in {"infrastructure-start-failure", "transient-provider-pre-terminal"},
        predecessor_attempt_id=predecessor_attempt_id,
    )


def select_terminal_attempt(records: tuple[TerminalRecord, ...]) -> TerminalRecord:
    """Select exactly as the approved one-retry policy permits."""

    if not records or len(records) > 2:
        raise ValueError("an experiment must contain one attempt or one eligible retry")
    first = records[0]
    if len(records) == 1:
        return first
    second = records[1]
    if not first.retry_eligible or second.predecessor_attempt_id != first.attempt_id:
        raise ValueError("second attempt is not an authorized retry of the first")
    return second
