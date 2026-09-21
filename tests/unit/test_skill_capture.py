from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cernora import CompletedExport, import_evidence_bundle_v2

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.skill_capture.audit import audit, skill_body
from cernora_reference_workflow.skill_capture.contracts import (
    SkillPlan,
    digest,
    replay,
    verify_export,
)
from cernora_reference_workflow.skill_capture.evaluation import (
    SkillCaptureAdapter,
    evaluate_export,
)
from cernora_reference_workflow.skill_capture.runtime import capture, publish_export


def extension_digest() -> str:
    from cernora_reference_workflow.skill_capture import runtime

    return digest(Path(runtime.__file__).with_name("extension.ts").read_bytes())


def make_plan() -> SkillPlan:
    return SkillPlan.model_validate_json(
        json.dumps(
            {
                "schema_version": "cernora.reference.skill-plan/v1",
                "case_id": "lookup-chain",
                "runtime_version": "0.85.1",
                "extension_sha256": extension_digest(),
                "provider": "deepseek",
                "model": "test-model",
                "base_url": "https://api.deepseek.com",
                "thinking": "medium",
                "skill_name": "lookup",
                "skill_version": "1",
                "skill_files": {
                    "SKILL.md": (
                        "---\nname: lookup\ndescription: Read a catalog.\n---\n"
                        "Use help and query recorded resources.\n"
                    )
                },
                "invocation": "explicit",
                "system": "Read-only evaluation.",
                "task": "Find leaf and parent status.",
                "tool_name": "catalog",
                "command_path": ["get"],
                "id_option": "--id",
                "objects": [
                    {
                        "object_id": "leaf",
                        "record": {"parent": "root", "state": "done"},
                        "source_sha256": "a" * 64,
                        "source_pointer": "/leaf",
                        "projection_version": "test/v1",
                    },
                    {
                        "object_id": "root",
                        "record": {"state": "ready"},
                        "source_sha256": "a" * 64,
                        "source_pointer": "/root",
                        "projection_version": "test/v1",
                    },
                ],
                "facts": [
                    {
                        "field": "leaf_state",
                        "object_id": "leaf",
                        "pointer": "/state",
                        "expected": "done",
                    },
                    {
                        "field": "parent_state",
                        "object_id": "root",
                        "pointer": "/state",
                        "expected": "ready",
                    },
                ],
                "dependency": {"first_id": "leaf", "pointer": "/parent", "next_id": "root"},
                "max_requests": 8,
                "max_tool_calls": 12,
                "max_output_tokens": 8192,
                "max_request_bytes": 262144,
                "timeout_seconds": 180,
                "retries": 0,
            }
        )
    )


@pytest.fixture
def plan() -> SkillPlan:
    return make_plan()


def native(plan: SkillPlan, variant: str = "pass", implicit: bool = False) -> dict[str, bytes]:
    body = skill_body(plan.skill_files["SKILL.md"])
    prompt = (
        plan.task
        if implicit or variant == "no_skill"
        else (
            '<skill name="lookup" location="/skills/lookup/SKILL.md">\n'
            f"{body}\n</skill>\n\n{plan.task}"
        )
    )
    transport: list[dict[str, Any]] = [
        {
            "kind": "model",
            "provider": plan.provider,
            "model": plan.model,
            "base_url": plan.base_url,
            "runtime_version": plan.runtime_version,
        },
        {"kind": "effective_prompt", "system": plan.system, "prompt": prompt},
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": plan.system},
        {"role": "user", "content": prompt},
    ]
    events: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    calls: list[tuple[str, dict[str, Any]]] = [
        (plan.tool_name, {"argv": ["get", "--id", "leaf", "-o", "json"]}),
        (plan.tool_name, {"argv": ["get", "--id", "root", "--output", "json"]}),
    ]
    if implicit:
        calls.insert(0, ("read", {"path": "/skills/lookup/SKILL.md"}))
    for index, (tool, args) in enumerate(calls, 1):
        if variant == "failed_tool" and index == 1:
            args = {"argv": ["write", "--id", "leaf"]}
        transport.append(
            {
                "kind": "request",
                "request_index": index,
                "payload": {
                    "model": plan.model,
                    "max_tokens": plan.max_output_tokens,
                    "tools": [
                        {"function": {"name": plan.tool_name}},
                        {"function": {"name": "read"}},
                    ],
                    "messages": json.loads(json.dumps(messages)),
                },
            }
        )
        transport.append({"kind": "response", "request_index": index, "status": 200})
        identity = f"call-{index}"
        assistant = {
            "role": "assistant",
            "stopReason": "toolUse",
            "content": [{"type": "toolCall", "id": identity, "name": tool, "arguments": args}],
            "usage": {"totalTokens": 12, "cost": {"total": 0.001}},
        }
        events.append({"type": "message_end", "message": assistant})
        transport.append({"kind": "usage", "request_index": index, "message": assistant})
        extra: dict[str, Any] = {}
        if tool == "read":
            stdout = plan.skill_files["SKILL.md"]
            code = 0
            extra = {
                "file": "SKILL.md",
                "start_line": 1,
                "end_line": len(stdout.split("\n")),
                "complete": True,
                "full_sha256": digest(stdout.encode()),
            }
        else:
            reply = replay(plan, args["argv"])
            code = reply["exit_code"]
            stdout = json.dumps({"evidence_id": identity, **reply})
        receipt = {
            "id": identity,
            "tool": tool,
            "args": args,
            "stdout": stdout,
            "stdout_sha256": digest(stdout.encode()),
            "exit_code": code,
            "request_index": index,
            **extra,
        }
        tools.append(receipt)
        events.extend(
            [
                {
                    "type": "tool_execution_start",
                    "toolCallId": identity,
                    "toolName": tool,
                    "args": args,
                },
                {
                    "type": "tool_execution_end",
                    "toolCallId": identity,
                    "toolName": tool,
                    "result": {
                        "content": [{"type": "text", "text": stdout}],
                        "details": {"exit_code": code, "evidence_id": identity},
                    },
                    "isError": False,
                },
            ]
        )
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": identity, "function": {"name": tool, "arguments": json.dumps(args)}}
                    ],
                },
                {"role": "tool", "tool_call_id": identity, "content": stdout},
            ]
        )
    index = len(calls) + 1
    transport.append(
        {
            "kind": "request",
            "request_index": index,
            "payload": {
                "model": plan.model,
                "max_tokens": plan.max_output_tokens,
                "tools": [{"function": {"name": plan.tool_name}}, {"function": {"name": "read"}}],
                "messages": messages,
            },
        }
    )
    transport.append({"kind": "response", "request_index": index, "status": 200})
    facts = {f.field: f.expected for f in plan.facts}
    if variant == "wrong":
        facts["leaf_state"] = "wrong"
    answer = json.dumps(
        {"facts": facts, "evidence_ids": [t["id"] for t in tools if t["tool"] == plan.tool_name]}
    )
    final = {
        "role": "assistant",
        "stopReason": "stop",
        "content": [{"type": "text", "text": answer}],
        "usage": {"totalTokens": 10, "cost": {"total": 0.001}},
    }
    if variant != "missing":
        events.append({"type": "message_end", "message": final})
        if variant != "missing_usage":
            transport.append({"kind": "usage", "request_index": index, "message": final})
    process = {
        "returncode": 0 if variant != "missing" else 124,
        "timed_out": variant == "missing",
        "wall_ms": 50,
        "runtime_version": plan.runtime_version,
        "extension_sha256": plan.extension_sha256,
        "stderr": "",
    }
    return {
        "plan.json": canonical_json_bytes(plan.model_dump(mode="json")),
        "process.json": canonical_json_bytes(process),
        "events.jsonl": b"\n".join(canonical_json_bytes(e) for e in events),
        "requests.jsonl": b"\n".join(canonical_json_bytes(e) for e in transport),
        "tools.jsonl": b"\n".join(canonical_json_bytes(t) for t in tools),
    }


def test_replay_discovery_options_and_errors(plan: SkillPlan) -> None:
    assert replay(plan, ["--help"])["exit_code"] == 0
    assert replay(plan, ["get", "--help"])["exit_code"] == 0
    assert replay(plan, ["get", "-o", "json", "--id", "root"])["stdout"] == {"state": "ready"}
    assert replay(plan, ["get", "--id", "leaf", "-o", "json"])["stdout"]["parent"] == "root"
    for args in (
        ["write"],
        ["get", "--id", "absent", "-o", "json"],
        ["get", "--id", "leaf", "--id", "root", "-o", "json"],
        ["get", "--context-id", "leaf", "-o", "json"],
    ):
        assert replay(plan, args)["exit_code"] != 0


@pytest.mark.parametrize(
    "variant,outcome",
    [
        ("pass", "pass"),
        ("wrong", "fail"),
        ("missing", "inconclusive"),
        ("no_skill", "pass"),
        ("missing_usage", "pass"),
        ("failed_tool", "fail"),
    ],
)
def test_import_evaluate_reload_and_separate_loading(
    tmp_path: Path, plan: SkillPlan, variant: str, outcome: str
) -> None:
    source = tmp_path / "export"
    publish_export(source, native(plan, variant), plan)
    outputs = []
    for i in range(3):
        result = evaluate_export(plan, source, tmp_path / str(i))
        assert result["outcome"] == outcome and result["strict_reload"]
        assert (result["skill_loading"] == "unknown") == (variant == "no_skill")
        assert result["usage_complete"] == (variant not in {"missing", "missing_usage"})
        outputs.append((tmp_path / str(i) / "evaluation" / "evaluation-report.json").read_bytes())
    assert outputs[0] == outputs[1] == outputs[2]


def test_implicit_loading_is_permitted(tmp_path: Path, plan: SkillPlan) -> None:
    raw = plan.model_dump(mode="json")
    raw["invocation"] = "implicit"
    plan = SkillPlan.model_validate_json(json.dumps(raw))
    source = tmp_path / "export"
    publish_export(source, native(plan, implicit=True), plan)
    result = evaluate_export(plan, source, tmp_path / "result")
    assert result["implicit_loading_observed"] and not result["explicit_loading_observed"]
    assert result["outcome"] == "pass"


def test_missing_loading_cannot_be_replaced_by_installation(plan: SkillPlan) -> None:
    result = audit(plan, native(plan, "no_skill"))
    assert result["skill_loading"] == "unknown" and result["complete"]


def test_export_tamper_extra_files_and_plan_drift(tmp_path: Path, plan: SkillPlan) -> None:
    source = tmp_path / "export"
    publish_export(source, native(plan), plan)
    (source / "extra").write_text("not declared")
    with pytest.raises(ValueError):
        verify_export(source)
    (source / "extra").unlink()
    (source / "events.jsonl").write_text("{}\n")
    with pytest.raises(ValueError):
        verify_export(source)
    other = tmp_path / "other"
    publish_export(other, native(plan), plan)
    changed = plan.model_copy(update={"skill_version": "2"})
    with pytest.raises(ValueError, match="selected external Plan"):
        SkillCaptureAdapter(changed).adapt(CompletedExport(other), tmp_path / "bundle")


def test_resealed_capture_does_not_invent_tool_delivery(plan: SkillPlan) -> None:
    files = native(plan)
    lines = [json.loads(x) for x in files["requests.jsonl"].splitlines()]
    for row in lines:
        if row["kind"] == "request" and row["request_index"] == 2:
            row["payload"]["messages"] = [
                m for m in row["payload"]["messages"] if m["role"] != "tool"
            ]
    files["requests.jsonl"] = b"\n".join(canonical_json_bytes(x) for x in lines)
    with pytest.raises(ValueError, match="delivered tool result"):
        audit(plan, files)


def test_configuration_unknown_fields_identity_and_reference_rejected(plan: SkillPlan) -> None:
    for key, value in [
        ("schema_version", "future"),
        ("max_requests", 9),
        ("retries", 1),
        ("extra", True),
    ]:
        data = plan.model_dump(mode="json")
        data[key] = value
        with pytest.raises(ValueError):
            SkillPlan.model_validate_json(json.dumps(data))
    data = plan.model_dump(mode="json")
    data["facts"][0]["expected"] = "fabricated"
    with pytest.raises(ValueError):
        SkillPlan.model_validate_json(json.dumps(data))


def test_acceptance_is_checked_before_runtime(tmp_path: Path, plan: SkillPlan) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(canonical_json_bytes(plan.model_dump(mode="json")))
    with pytest.raises(ValueError, match="acceptance"):
        capture(path, tmp_path / "export", tmp_path / "absent-auth", "wrong")


def test_normalization_cannot_replace_answer_with_oracle(tmp_path: Path, plan: SkillPlan) -> None:
    source = tmp_path / "export"
    publish_export(source, native(plan, "wrong"), plan)
    adapter = SkillCaptureAdapter(plan)
    bundle = adapter.adapt(CompletedExport(source), tmp_path / "bundle")
    # Rehash a forged normalized answer: the Profile must compare with embedded native evidence.
    payload = json.loads(bundle.bundle_path.read_bytes())
    wrapper = json.loads(payload["terminal"]["answer"]["content"])
    wrapper["raw_answer"] = json.dumps(
        {"facts": {f.field: f.expected for f in plan.facts}, "evidence_ids": ["call-1", "call-2"]}
    )
    answer = canonical_json_bytes(wrapper)
    (bundle.bundle_path.parent / "answer.json").write_bytes(answer)
    for artifact in payload["artifacts"]:
        if artifact["artifact_id"] == "answer":
            artifact.update(sha256=digest(answer), size_bytes=len(answer))
    payload["terminal"]["answer"].update(content=answer.decode(), sha256=digest(answer))
    payload["terminal"]["answer"]["artifact"]["sha256"] = digest(answer)
    payload.pop("bundle_sha256")
    payload["bundle_sha256"] = digest(canonical_json_bytes(payload))
    bundle.bundle_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ValueError):
        import_evidence_bundle_v2(
            profile=adapter.profile, bundle_path=bundle.bundle_path, output=tmp_path / "import"
        )


@pytest.mark.parametrize("invocation", ["explicit", "implicit"])
def test_installed_pi_offline_transport(
    tmp_path: Path, plan: SkillPlan, monkeypatch: pytest.MonkeyPatch, invocation: str
) -> None:
    """Actual pi SDK/extension plumbing with synthetic SSE; no model quality claim."""
    import shutil
    import subprocess

    pi = shutil.which("pi")
    if not pi or subprocess.check_output([pi, "--version"], text=True).strip() != "0.85.1":
        pytest.skip("optional local pi 0.85.1 integration check")
    payload = plan.model_dump(mode="json")
    payload.update(model="deepseek-v4-flash", thinking="off", invocation=invocation)
    selected = SkillPlan.model_validate_json(json.dumps(payload))
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(payload))
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"deepseek": {"type": "api_key", "key": "offline-only-key"}}))
    stub = tmp_path / "offline.ts"
    stub.write_text(
        """export default function () {
let n=0;
globalThis.fetch=async (_input, init) => {
 const p=JSON.parse(init.body); n++;
 const implicit=!JSON.stringify(p.messages[1]).includes('<skill name=');
 const step=n-(implicit?1:0);
 const call=implicit && n===1
 ? {name:'read',arguments:JSON.stringify({path:p.messages[0].content
      .match(/<location>(.*?)<\\/location>/)[1]})}
 : {name:'catalog',arguments:JSON.stringify({argv:
      ['get','--id',step===1?'leaf':'root','-o','json']})};
 const delta=step<3
 ? {tool_calls:[{index:0,id:'call-'+n,type:'function',function:call}]}
 : {content:JSON.stringify({facts:{leaf_state:'done',parent_state:'ready'},
      evidence_ids:['call-'+(implicit?2:1),'call-'+(implicit?3:2)]})};
 const data={id:'offline-'+n,object:'chat.completion.chunk',created:1,model:p.model,
      choices:[{index:0,delta,finish_reason:null}]};
 const end={...data,choices:[{index:0,delta:{},finish_reason:step<3?'tool_calls':'stop'}],
      usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}};
 return new Response('data: '+JSON.stringify(data)+'\\n\\ndata: '+JSON.stringify(end)+
      '\\n\\ndata: [DONE]\\n\\n',
      {status:200,headers:{'content-type':'text/event-stream'}});
};
}"""
    )
    original = subprocess.Popen

    def offline_popen(*args: Any, **kwargs: Any) -> Any:
        if "env" in kwargs:
            argv = list(args[0])
            position = argv.index("--extension")
            argv[position:position] = ["--extension", str(stub)]
            args = (argv, *args[1:])
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", offline_popen)
    capture(source, tmp_path / "export", auth, selected.sha256)
    result = evaluate_export(selected, tmp_path / "export", tmp_path / "evaluation")
    assert result["outcome"] == "pass"
    assert result["skill_loading"] == "observed"
    assert result[invocation + "_loading_observed"] is True
    assert result["requests"] == (4 if invocation == "implicit" else 3)
