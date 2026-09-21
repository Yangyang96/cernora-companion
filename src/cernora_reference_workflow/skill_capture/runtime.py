"""Capture one pi invocation and atomically publish a closed native export."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    load_json_file,
    read_regular_file_bytes,
)
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.skill_capture.contracts import SkillPlan, digest, verify_export


def publish_export(output: Path, payloads: dict[str, bytes], plan: SkillPlan) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".skill-export-", dir=output.parent) as tmp:
        staging = Path(tmp) / "closed"
        staging.mkdir()
        for name, data in payloads.items():
            if Path(name).name != name:
                raise ContractError("export filenames must be flat")
            (staging / name).write_bytes(data)
        manifest = {
            "schema_version": "cernora.reference.skill-export/v1",
            "plan_sha256": plan.sha256,
            "files": {k: digest(v) for k, v in payloads.items()},
        }
        (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        verify_export(staging)
        # publication requires siblings; the closed directory is moved to its final sibling first.
        sibling = output.parent / Path(tmp).name.replace(".skill-export-", ".skill-publish-")
        staging.rename(sibling)
        try:
            atomic_publish_directory(sibling, output)
        finally:
            if sibling.exists():
                shutil.rmtree(sibling)


def capture(plan_path: Path, output: Path, auth_path: Path, accepted_sha256: str) -> None:
    plan = SkillPlan.read(plan_path)
    if accepted_sha256 != plan.sha256:
        raise ContractError("explicit Plan acceptance does not match")
    if output.exists() or output.is_symlink():
        raise ContractError("capture output must be new")
    pi = shutil.which("pi")
    if (
        pi is None
        or subprocess.check_output([pi, "--version"], text=True).strip() != plan.runtime_version
    ):
        raise ContractError("pi runtime version mismatch")
    extension_bytes = read_regular_file_bytes(Path(__file__).with_name("extension.ts"))
    if digest(extension_bytes) != plan.extension_sha256:
        raise ContractError("capture extension differs from accepted Plan")
    auth = load_json_file(auth_path)
    credential = auth.get(plan.provider) if isinstance(auth, dict) else None
    if (
        not isinstance(credential, dict)
        or credential.get("type") != "api_key"
        or not isinstance(credential.get("key"), str)
        or not credential["key"]
    ):
        raise ContractError("provider API key credential unavailable")
    # Deliberately copy only the selected provider key, not unrelated account credentials.
    with tempfile.TemporaryDirectory(prefix="cernora-skill-") as tmp:
        root = Path(tmp)
        home = root / "home"
        work = root / "work"
        out = root / "capture"
        skill = work / "skills" / plan.skill_name
        for path in (home, work, out, skill):
            path.mkdir(parents=True, exist_ok=True)
        (home / "auth.json").write_bytes(
            canonical_json_bytes({plan.provider: {"type": "api_key", "key": credential["key"]}})
        )
        (home / "auth.json").chmod(0o600)
        (home / "settings.json").write_bytes(
            canonical_json_bytes({"compaction": {"enabled": False}, "retry": {"enabled": False}})
        )
        for name, text in plan.skill_files.items():
            path = skill / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        local_plan = root / "plan.json"
        local_plan.write_bytes(canonical_json_bytes(plan.model_dump(mode="json")))
        extension = root / "extension.ts"
        extension.write_bytes(read_regular_file_bytes(Path(__file__).with_name("extension.ts")))
        for name in ("events.jsonl", "requests.jsonl", "tools.jsonl"):
            (out / name).touch()
        env = {
            k: v
            for k, v in os.environ.items()
            if k in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        }
        env.update(
            {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(home),
                "PI_OFFLINE": "1",
                "PI_TELEMETRY": "0",
                "PI_SKIP_VERSION_CHECK": "1",
                "CERNORA_SKILL_PLAN": str(local_plan),
                "CERNORA_SKILL_ROOT": str(skill),
                "CERNORA_CAPTURE_OUT": str(out),
                "CERNORA_CAPTURE_PYTHON": sys.executable,
            }
        )
        task = (
            ("/skill:" + plan.skill_name + " " + plan.task)
            if plan.invocation == "explicit"
            else plan.task
        )
        argv = [
            pi,
            "--approve",
            "--print",
            "--mode",
            "json",
            "--provider",
            plan.provider,
            "--model",
            plan.model,
            "--thinking",
            plan.thinking,
            "--no-extensions",
            "--extension",
            str(extension),
            "--no-builtin-tools",
            "--tools",
            plan.tool_name + ",read",
            "--skill",
            str(skill),
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--system-prompt",
            plan.system,
            "--session-dir",
            str(root / "sessions"),
            "--",
            task,
        ]
        started = time.monotonic_ns()
        timed_out = False
        with (
            (out / "events.jsonl").open("wb") as stdout,
            (root / "stderr.txt").open("wb") as stderr,
        ):
            process = subprocess.Popen(
                argv,
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                code = process.wait(timeout=plan.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        ended = time.monotonic_ns()
        receipt: dict[str, Any] = {
            "returncode": code,
            "timed_out": timed_out,
            "wall_ms": (ended - started) // 1_000_000,
            "runtime_version": plan.runtime_version,
            "extension_sha256": digest(extension.read_bytes()),
            "stderr": (root / "stderr.txt").read_text(errors="replace"),
        }
        payloads = {
            name: (out / name).read_bytes()
            for name in ("events.jsonl", "requests.jsonl", "tools.jsonl")
        }
        payloads.update(
            {
                "plan.json": canonical_json_bytes(plan.model_dump(mode="json")),
                "process.json": canonical_json_bytes(receipt),
            }
        )
        if any(credential["key"].encode() in raw for raw in payloads.values()):
            raise ContractError("credential detected in capture; export withheld")
        publish_export(output, payloads, plan)
