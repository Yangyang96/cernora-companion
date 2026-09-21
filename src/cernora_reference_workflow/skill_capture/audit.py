"""Offline native-capture integrity and loading/accounting observations."""

from __future__ import annotations

import math
import re
from typing import Any

from cernora_reference_workflow.common import ContractError, canonical_json_bytes, load_json_bytes
from cernora_reference_workflow.skill_capture.contracts import SkillPlan, digest, replay


def rows(raw: bytes) -> list[dict[str, Any]]:
    result = []
    for line in raw.splitlines():
        value = load_json_bytes(line)
        if not isinstance(value, dict):
            raise ContractError("capture rows must be objects")
        result.append(value)
    return result


def texts(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(texts(item) for item in value)
    if isinstance(value, dict):
        return str(value["text"]) if value.get("type") == "text" else ""
    return ""


def skill_body(raw: str) -> str:
    return re.sub(r"^---\r?\n.*?\r?\n---(?:\r?\n|$)", "", raw, count=1, flags=re.S).strip()


def audit(plan: SkillPlan, files: dict[str, bytes]) -> dict[str, Any]:
    events = rows(files["events.jsonl"])
    transport = rows(files["requests.jsonl"])
    tools = rows(files["tools.jsonl"])
    process = load_json_bytes(files["process.json"])
    if (
        not isinstance(process, dict)
        or set(process)
        != {"returncode", "timed_out", "wall_ms", "runtime_version", "extension_sha256", "stderr"}
        or type(process["returncode"]) is not int
        or type(process["timed_out"]) is not bool
        or type(process["wall_ms"]) is not int
        or process["wall_ms"] < 0
        or process["runtime_version"] != plan.runtime_version
        or process["extension_sha256"] != plan.extension_sha256
    ):
        raise ContractError("invalid process receipt")
    if any(
        t.get("kind")
        not in {"model", "effective_prompt", "request", "response", "usage", "blocked"}
        for t in transport
    ):
        raise ContractError("unknown transport event")
    requests = [t for t in transport if t["kind"] == "request"]
    indices = [r.get("request_index") for r in requests]
    if indices != list(range(1, len(requests) + 1)) or len(requests) > plan.max_requests:
        raise ContractError("request identity/budget mismatch")
    models = [t for t in transport if t["kind"] == "model"]
    if len(models) != 1 or any(
        models[0].get(k) != v
        for k, v in {
            "provider": plan.provider,
            "model": plan.model,
            "base_url": plan.base_url,
            "runtime_version": plan.runtime_version,
        }.items()
    ):
        raise ContractError("effective model metadata unavailable or mismatched")
    for request in requests:
        payload = request["payload"]
        if (
            payload.get("model") != plan.model
            or payload.get("max_tokens") != plan.max_output_tokens
            or len(canonical_json_bytes(payload)) > plan.max_request_bytes
            or {t["function"]["name"] for t in payload.get("tools", [])} != {plan.tool_name, "read"}
        ):
            raise ContractError("effective request configuration mismatch")
    responses = [t for t in transport if t["kind"] == "response"]
    response_complete = (
        len(responses) == len(requests)
        and [t.get("request_index") for t in responses] == indices
        and all(type(t.get("status")) is int and 200 <= t["status"] < 300 for t in responses)
    )
    prompts = [t for t in transport if t["kind"] == "effective_prompt"]
    if len(prompts) != 1 or plan.system not in prompts[0]["system"]:
        raise ContractError("effective prompt unavailable")
    prompt = prompts[0]["prompt"]
    if prompt != plan.task and not (
        isinstance(prompt, str)
        and prompt.startswith('<skill name="' + plan.skill_name + '"')
        and skill_body(plan.skill_files["SKILL.md"]) in prompt
        and prompt.endswith("</skill>\n\n" + plan.task)
    ):
        raise ContractError("task instruction drift")
    for request in requests:
        messages = request["payload"].get("messages", [])
        if (
            len(messages) < 2
            or messages[0].get("role") != "system"
            or texts(messages[0].get("content")) != prompts[0]["system"]
            or messages[1].get("role") != "user"
            or texts(messages[1].get("content")) != prompts[0]["prompt"]
        ):
            raise ContractError("effective instruction drift")
    assistants = [
        e["message"]
        for e in events
        if e.get("type") == "message_end" and e.get("message", {}).get("role") == "assistant"
    ]
    starts = {e["toolCallId"]: e for e in events if e.get("type") == "tool_execution_start"}
    ends = {e["toolCallId"]: e for e in events if e.get("type") == "tool_execution_end"}
    ids = [t.get("id") for t in tools]
    if len(ids) != len(set(ids)) or len(ids) > plan.max_tool_calls:
        raise ContractError("tool identity/budget mismatch")
    if len(starts) != sum(e.get("type") == "tool_execution_start" for e in events) or len(
        ends
    ) != sum(e.get("type") == "tool_execution_end" for e in events):
        raise ContractError("duplicate tool lifecycle event")
    incomplete = set(starts) != set(ids) or set(ends) != set(ids)
    loaded_resources = []
    for tool in tools:
        if (
            tool["id"] not in starts
            or tool["id"] not in ends
            or starts[tool["id"]]["toolName"] != tool["tool"]
            or canonical_json_bytes(starts[tool["id"]]["args"])
            != canonical_json_bytes(tool["args"])
            or ends[tool["id"]]["result"]["content"] != [{"type": "text", "text": tool["stdout"]}]
            or digest(tool["stdout"].encode()) != tool["stdout_sha256"]
        ):
            raise ContractError("tool receipt contradicts native events")
        if tool.get("request_index") not in indices:
            raise ContractError("tool request identity unavailable")
        ordinal = tool["request_index"] - 1
        if ordinal >= len(assistants) or not any(
            c.get("type") == "toolCall"
            and c.get("id") == tool["id"]
            and c.get("name") == tool["tool"]
            and c.get("arguments") == tool["args"]
            for c in assistants[ordinal].get("content", [])
        ):
            raise ContractError("tool invocation not bound to model response")
        # Every subsequent request must retain the delivered prior tool result.
        for request in requests:
            if request["request_index"] > tool["request_index"] and not any(
                m.get("role") == "tool"
                and m.get("tool_call_id") == tool["id"]
                and texts(m.get("content")) == tool["stdout"]
                for m in request["payload"].get("messages", [])
            ):
                raise ContractError("delivered tool result missing from subsequent request")
        if tool["tool"] == plan.tool_name:
            expected = {"evidence_id": tool["id"], **replay(plan, tool["args"]["argv"])}
            if (
                load_json_bytes(tool["stdout"].encode()) != expected
                or tool["exit_code"] != expected["exit_code"]
            ):
                raise ContractError("replay receipt contradicts frozen data")
        elif tool["tool"] == "read":
            if tool["exit_code"] == 0:
                content = plan.skill_files.get(str(tool.get("file")))
                if content is None or digest(content.encode()) != tool.get("full_sha256"):
                    raise ContractError("Skill resource identity mismatch")
                start, end = tool["start_line"], tool["end_line"]
                if (
                    type(start) is not int
                    or type(end) is not int
                    or not 1 <= start <= end <= len(content.split("\n"))
                ):
                    raise ContractError("Skill read range mismatch")
                if "\n".join(content.split("\n")[start - 1 : end]) != tool["stdout"]:
                    raise ContractError("Skill read bytes mismatch")
                delivered = any(
                    any(
                        m.get("role") == "tool"
                        and m.get("tool_call_id") == tool["id"]
                        and texts(m.get("content")) == tool["stdout"]
                        for m in r["payload"].get("messages", [])
                    )
                    for r in requests
                )
                loaded_resources.append(
                    {
                        "file": tool["file"],
                        "sha256": tool["full_sha256"],
                        "start_line": start,
                        "end_line": end,
                        "delivered": delivered,
                    }
                )
        else:
            raise ContractError("undeclared captured tool")
    assistants = [
        e["message"]
        for e in events
        if e.get("type") == "message_end" and e.get("message", {}).get("role") == "assistant"
    ]
    last = assistants[-1] if assistants else None
    raw_answer = (
        "\n".join(c["text"] for c in last.get("content", []) if c.get("type") == "text")
        if last
        else None
    )
    complete = (
        process["returncode"] == 0
        and not process["timed_out"]
        and not incomplete
        and bool(requests)
        and response_complete
        and len(assistants) == len(requests)
        and last is not None
        and last.get("stopReason") in {"stop", "length"}
        and bool(raw_answer)
        and not any(c.get("type") == "toolCall" for c in last.get("content", []))
        and not any(t["kind"] == "blocked" for t in transport)
    )
    body = skill_body(plan.skill_files["SKILL.md"])
    explicit = bool(body) and any(
        any(
            m.get("role") == "user"
            and '<skill name="' + plan.skill_name + '"' in texts(m.get("content"))
            and body in texts(m.get("content"))
            for m in r["payload"].get("messages", [])
        )
        for r in requests
    )
    covered: set[int] = set()
    for resource in loaded_resources:
        if resource["file"] == "SKILL.md" and resource["delivered"]:
            covered.update(range(resource["start_line"], resource["end_line"] + 1))
    implicit = covered == set(range(1, len(plan.skill_files["SKILL.md"].split("\n")) + 1))
    usage = []
    for index in range(1, len(requests) + 1):
        matches = [
            t["message"]
            for t in transport
            if t["kind"] == "usage" and t.get("request_index") == index
        ]
        message = matches[0] if len(matches) == 1 else {}
        measured = (index <= len(assistants) and message == assistants[index - 1]) and message.get(
            "stopReason"
        ) in {
            "stop",
            "length",
            "toolUse",
        }
        token = message.get("usage", {}).get("totalTokens")
        cost = message.get("usage", {}).get("cost", {}).get("total")
        token_ok = measured and type(token) is int and token > 0
        cost_ok = measured and type(cost) in {int, float} and math.isfinite(cost) and cost >= 0
        usage.append(
            {
                "request_index": index,
                "tokens": token if token_ok else None,
                "runtime_estimated_cost": cost if cost_ok else None,
            }
        )
    usage_complete = bool(usage) and all(u["tokens"] is not None for u in usage)
    return {
        "complete": complete,
        "raw_answer": raw_answer,
        "stop_reason": last.get("stopReason") if last else None,
        "skill_loading": "observed" if explicit or implicit else "unknown",
        "explicit_loading_observed": explicit,
        "implicit_loading_observed": implicit,
        "resources": loaded_resources,
        "tool_calls": len(tools),
        "wall_ms": process["wall_ms"],
        "requests": len(requests),
        "usage_complete": usage_complete,
        "usage": usage,
        "total_tokens": sum(u["tokens"] for u in usage) if usage_complete else None,
        "observed_tokens": sum(u["tokens"] for u in usage if u["tokens"] is not None),
        "cost_currency": "unverified",
        "cost_scope": "runtime_estimate_not_billing",
        "tools": tools,
        "source_files": {name: digest(raw) for name, raw in files.items()},
    }
