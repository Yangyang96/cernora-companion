"""Convert one completed pi session into the Harbor ATIF trajectory contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from harbor.utils.trajectory_utils import format_trajectory_json

from cernora_reference_workflow.common import ContractError

_ACCEPTED_ROLES = ("user", "assistant", "toolResult")


def _iso_timestamp(value: Any) -> str | None:
    """Convert one pi Unix-millisecond timestamp into an ISO 8601 string."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _text_content(content: Any) -> str:
    """Join the text of a pi message content scalar or block array."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _metrics_from_usage(usage: Any) -> Metrics | None:
    if not isinstance(usage, dict):
        return None
    try:
        return Metrics(
            prompt_tokens=usage.get("input") or 0,
            completion_tokens=usage.get("output") or 0,
            cached_tokens=usage.get("cacheRead") or 0,
            extra={
                "cost": usage.get("cost") if isinstance(usage.get("cost"), dict) else None,
                "total_tokens": usage.get("totalTokens") or 0,
            },
        )
    except ValueError as exc:
        raise ContractError("pi session usage record is not representable as metrics") from exc


def _load_session(
    session_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Load one pi session file into its header entry, agent message list, and skip count.

    Entries the ATIF conversion does not represent — non-``message`` entries such as
    compaction records and messages with roles outside ``_ACCEPTED_ROLES`` — are counted
    instead of being silently discarded, so the Trajectory's agent metadata can surface
    exactly how much of the session was dropped.
    """

    header: dict[str, Any] | None = None
    messages: list[dict[str, Any]] = []
    skipped = 0
    with session_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ContractError(f"pi session line is not JSON: {session_path.name}") from exc
            if not isinstance(entry, dict):
                raise ContractError(f"pi session line is not an object: {session_path.name}")
            if header is None:
                if entry.get("type") != "session":
                    raise ContractError(
                        f"pi session file does not start with a session header: {session_path.name}"
                    )
                header = entry
                continue
            if entry.get("type") != "message":
                skipped += 1
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or message.get("role") not in _ACCEPTED_ROLES:
                skipped += 1
                continue
            messages.append(message)
    if header is None:
        raise ContractError(f"pi session file is empty: {session_path.name}")
    return header, messages, skipped


def convert_pi_session(
    session_path: Path,
    *,
    agent_version: str | None,
) -> Trajectory:
    """Convert one pi session JSONL file into an ATIF-v1.7 trajectory."""

    header, messages, skipped = _load_session(session_path)
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ContractError("pi session header is missing a session id")

    step_specs: list[dict[str, Any]] = []
    call_owner: dict[str, int] = {}
    for message in messages:
        role = message.get("role")
        timestamp = _iso_timestamp(message.get("timestamp"))
        if role == "user":
            step_specs.append(
                {
                    "source": "user",
                    "message": _text_content(message.get("content")),
                    "timestamp": timestamp,
                }
            )
        elif role == "assistant":
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text" and isinstance(block.get("text"), str):
                        text_parts.append(block["text"])
                    elif block_type == "thinking" and isinstance(block.get("thinking"), str):
                        thinking_parts.append(block["thinking"])
                    elif block_type == "toolCall":
                        call_id = block.get("id")
                        tool_name = block.get("name")
                        arguments = block.get("arguments")
                        if isinstance(call_id, str) and isinstance(tool_name, str):
                            tool_calls.append(
                                {
                                    "tool_call_id": call_id,
                                    "function_name": tool_name,
                                    "arguments": arguments
                                    if isinstance(arguments, dict)
                                    else {"value": arguments},
                                }
                            )
                            call_owner[call_id] = len(step_specs)
            step_specs.append(
                {
                    "source": "agent",
                    "message": "\n\n".join(part for part in text_parts if part),
                    "reasoning": "\n\n".join(part for part in thinking_parts if part) or None,
                    "tool_calls": tool_calls,
                    "results": [],
                    "model_name": message.get("model")
                    if isinstance(message.get("model"), str)
                    else None,
                    "metrics": _metrics_from_usage(message.get("usage")),
                    "timestamp": timestamp,
                }
            )
        elif role == "toolResult":
            call_id = message.get("toolCallId")
            if not isinstance(call_id, str) or call_id not in call_owner:
                raise ContractError("pi session tool result has no matching assistant tool call")
            step_specs[call_owner[call_id]]["results"].append(
                {
                    "source_call_id": call_id,
                    "content": _text_content(message.get("content")),
                    "is_error": bool(message.get("isError")),
                }
            )

    steps: list[Step] = []
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cost = 0.0
    total_tokens = 0
    default_model: str | None = None
    for spec in step_specs:
        metrics = spec.get("metrics")
        if spec["source"] == "agent":
            if isinstance(metrics, Metrics):
                total_input += metrics.prompt_tokens or 0
                total_output += metrics.completion_tokens or 0
                total_cache_read += metrics.cached_tokens or 0
                metrics_extra = metrics.extra or {}
                cost_entry = metrics_extra.get("cost")
                if isinstance(cost_entry, dict):
                    total_cost += cost_entry.get("total") or 0.0
                total_tokens += metrics_extra.get("total_tokens") or 0
            default_model = spec.get("model_name") or default_model
        results = spec.get("results") or []
        observation = (
            Observation(
                results=[
                    ObservationResult(
                        source_call_id=result["source_call_id"],
                        content=result["content"],
                        extra={"is_error": result["is_error"]},
                    )
                    for result in results
                ]
            )
            if results
            else None
        )
        steps.append(
            Step(
                step_id=len(steps) + 1,
                timestamp=spec.get("timestamp"),
                source=spec["source"],
                message=spec["message"],
                reasoning_content=spec.get("reasoning") if spec["source"] == "agent" else None,
                tool_calls=[ToolCall(**call) for call in spec.get("tool_calls", [])] or None,
                observation=observation,
                model_name=spec.get("model_name"),
                metrics=metrics if spec["source"] == "agent" else None,
                llm_call_count=1 if spec["source"] == "agent" else None,
            )
        )
    if not steps:
        raise ContractError("pi session contains no agent messages")

    final_metrics = FinalMetrics(
        total_prompt_tokens=total_input or None,
        total_completion_tokens=total_output or None,
        total_cached_tokens=total_cache_read or None,
        total_cost_usd=total_cost if total_cost else None,
        total_steps=len(steps),
        extra={"usage_total_tokens": total_tokens},
    )
    trajectory = Trajectory(
        schema_version="ATIF-v1.7",
        session_id=session_id,
        agent=Agent(
            name="pi",
            version=agent_version or "unknown",
            model_name=default_model,
            extra={
                "cwd": header.get("cwd") if isinstance(header.get("cwd"), str) else None,
                "session_version": header.get("version"),
                "skipped_entries": skipped,
            },
        ),
        steps=steps,
        final_metrics=final_metrics,
    )
    return trajectory


def write_pi_trajectory(
    sessions_root: Path,
    destination: Path,
    *,
    agent_version: str | None,
) -> Trajectory:
    """Write the ATIF trajectory for the exactly one session under ``sessions_root``."""

    if not sessions_root.is_dir():
        raise ContractError("pi session directory is missing")
    session_files = sorted(sessions_root.glob("*.jsonl"))
    if len(session_files) != 1:
        raise ContractError(f"expected exactly one pi session file; observed {len(session_files)}")
    trajectory = convert_pi_session(session_files[0], agent_version=agent_version)
    destination.write_text(
        format_trajectory_json(trajectory.to_json_dict()),
        encoding="utf-8",
    )
    return trajectory


__all__ = [
    "convert_pi_session",
    "write_pi_trajectory",
]
