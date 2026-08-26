"""Verify the local M2 wheels against a closed Execution Pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

EXPECTED_CORE_WHEEL_SHA256 = "53276a35b137e4997ea5cdf843e2d23323583c4b34ac87a9cd997a08d44e6704"
_NETWORK_DENIED_CLI = r"""
import runpy
import socket
import sys

def deny_network(*args, **kwargs):
    raise RuntimeError("wheel-only verifier denied a network operation")

class DeniedSocket(socket.socket):
    def connect(self, *args, **kwargs):
        deny_network(*args, **kwargs)

    def connect_ex(self, *args, **kwargs):
        deny_network(*args, **kwargs)

socket.socket = DeniedSocket
socket.create_connection = deny_network
socket.getaddrinfo = deny_network
sys.argv = ["experiment", *sys.argv[1:]]
runpy.run_module("cernora_reference_workflow.cli", run_name="__main__")
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _isolated_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.upper().endswith("_PROXY") or name in {
            "CODEX_AUTH_JSON_PATH",
            "CERNORA_HTTP_PROXY",
            "CERNORA_HTTPS_PROXY",
            "CERNORA_ALL_PROXY",
        }:
            environment.pop(name, None)
    environment.pop("PYTHONPATH", None)
    return environment


def _verify_python(
    *,
    python_version: str,
    core_wheel: Path,
    companion_wheel: Path,
    execution_pack: Path,
    expected_outcomes: tuple[int, int, int, int],
) -> None:
    with tempfile.TemporaryDirectory(prefix=f"cernora-m2-wheels-{python_version}-") as directory:
        work = Path(directory)
        venv = work / "venv"
        _run(["uv", "venv", "--python", python_version, "--seed", str(venv)], cwd=work)
        python = venv / "bin/python"
        _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--offline",
                str(core_wheel),
                str(companion_wheel),
            ],
            cwd=work,
        )

        isolated_env = _isolated_environment()
        outputs = tuple(work / f"summary-{index}" for index in range(3))
        for output in outputs:
            _run(
                [
                    str(python),
                    "-I",
                    "-c",
                    _NETWORK_DENIED_CLI,
                    "summarize",
                    str(execution_pack),
                    "--output",
                    str(output),
                ],
                cwd=work,
                env=isolated_env,
            )

        expected_json = json.dumps(expected_outcomes, separators=(",", ":"))
        code = """
import importlib.metadata
import json
import sys
from pathlib import Path

import cernora
import cernora_reference_workflow
from cernora import reload_batch_summary

root = Path(sys.argv[1])
expected = tuple(json.loads(sys.argv[2]))
summary = reload_batch_summary(root)
outcomes = summary.overall.outcomes
actual = (
    outcomes.passed,
    outcomes.behavioral_failed,
    outcomes.evaluation_invalid,
    outcomes.infrastructure_unavailable,
)
assert actual == expected, (actual, expected)
assert importlib.metadata.version("cernora") == "0.1.3"
assert importlib.metadata.version("cernora-reference-workflow") == "0.2.1"
assert "site-packages" in str(Path(cernora.__file__).resolve())
assert "site-packages" in str(Path(cernora_reference_workflow.__file__).resolve())
payload = json.loads((root / "batch-summary.json").read_text(encoding="utf-8"))
forbidden = {"winner", "ranking", "delta", "confidence_interval", "pass_at_k", "improvement"}

def keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from keys(child)

assert forbidden.isdisjoint(keys(payload))
"""
        for output in outputs:
            _run(
                [str(python), "-I", "-c", code, str(output), expected_json],
                cwd=work,
                env=isolated_env,
            )
        trees = tuple(_tree(output) for output in outputs)
        if not trees[0] == trees[1] == trees[2]:
            raise RuntimeError("three wheel-only Batch Summary trees are not byte-identical")
    print(f"M2 wheels verified on CPython {python_version}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-wheel", type=Path, required=True)
    parser.add_argument("--companion-wheel", type=Path, required=True)
    parser.add_argument("--execution-pack", type=Path, required=True)
    parser.add_argument(
        "--expected-outcomes",
        default="5,0,6,1",
        help="passed,behavioral-failed,evaluation-invalid,infrastructure-unavailable",
    )
    parser.add_argument(
        "--python",
        action="append",
        choices=("3.12", "3.13"),
        dest="python_versions",
    )
    args = parser.parse_args()

    core_wheel = args.core_wheel.resolve(strict=True)
    companion_wheel = args.companion_wheel.resolve(strict=True)
    execution_pack = args.execution_pack.resolve(strict=True)
    if _sha256(core_wheel) != EXPECTED_CORE_WHEEL_SHA256:
        raise RuntimeError("Core 0.1.3 wheel digest does not match the accepted candidate")
    try:
        expected_outcomes = tuple(int(item) for item in args.expected_outcomes.split(","))
    except ValueError as exc:
        raise RuntimeError("--expected-outcomes must contain four integers") from exc
    if len(expected_outcomes) != 4 or any(item < 0 for item in expected_outcomes):
        raise RuntimeError("--expected-outcomes must contain four non-negative integers")
    checked_outcomes = (
        expected_outcomes[0],
        expected_outcomes[1],
        expected_outcomes[2],
        expected_outcomes[3],
    )

    versions = tuple(args.python_versions) if args.python_versions else ("3.12", "3.13")
    for version in versions:
        _verify_python(
            python_version=version,
            core_wheel=core_wheel,
            companion_wheel=companion_wheel,
            execution_pack=execution_pack,
            expected_outcomes=checked_outcomes,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
