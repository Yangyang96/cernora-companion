"""Verify the local M3 wheels with an offline authority-bound comparison."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from cernora_reference_workflow.common import read_regular_file_bytes

EXPECTED_CORE_WHEEL_SHA256 = "5b847837b7182b3ece8054eb5187fde4f835582787b406ea4a7f2f8bd2987a4c"
ROOT = Path(__file__).resolve().parents[1]

_NETWORK_DENIED_COMPARE = r"""
import runpy
import socket
import sys
from pathlib import Path

from cernora import BatchInput, summarize_batch

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

batch_input = BatchInput.model_validate_json(Path(sys.argv[1]).read_bytes())
summarize_batch(batch_input, Path(sys.argv[4]))
sys.argv = [
    "experiment",
    "compare",
    sys.argv[4],
    "--run-plan",
    sys.argv[2],
    "--plan",
    sys.argv[3],
    "--output",
    sys.argv[5],
]
runpy.run_module("cernora_reference_workflow.cli", run_name="__main__")
"""

_STRICT_RELOAD = r"""
import importlib.metadata
import json
import sys
from pathlib import Path

import cernora
import cernora_reference_workflow
from cernora import reload_comparison_package

root = Path(sys.argv[1])
package = reload_comparison_package(root)
assert package.summary.comparable
assert package.summary.conclusion == "uncertain"
assert package.comparison_input.treatment.changes[0].kind == "prompt_instruction"
assert importlib.metadata.version("cernora") == "0.1.4"
assert importlib.metadata.version("cernora-reference-workflow") == "0.3.0"
assert "site-packages" in str(Path(cernora.__file__).resolve())
assert "site-packages" in str(Path(cernora_reference_workflow.__file__).resolve())

payload = json.loads((root / "comparison-summary.json").read_text(encoding="utf-8"))
forbidden = {"winner", "ranking", "promotion", "p_value", "p-value"}

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


def _sha256(path: Path) -> str:
    return hashlib.sha256(read_regular_file_bytes(path, maximum=None)).hexdigest()


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


def _verify_fixture(fixture: Path) -> tuple[Path, Path, Path]:
    expected = (
        fixture / "batch-input.json",
        fixture / "controlled-run-plan.json",
        fixture / "comparison-plan.json",
    )
    if any(not path.is_file() or path.is_symlink() or not path.read_bytes() for path in expected):
        raise RuntimeError("M3 wheel verifier fixture is incomplete")
    return expected


def _verify_python(
    *,
    python_version: str,
    core_wheel: Path,
    companion_wheel: Path,
    fixture: Path,
) -> None:
    batch_input, run_plan, comparison_plan = _verify_fixture(fixture)
    with tempfile.TemporaryDirectory(prefix=f"cernora-m3-wheels-{python_version}-") as directory:
        work = Path(directory)
        venv = work / "venv"
        isolated_env = _isolated_environment()
        _run(
            ["uv", "venv", "--offline", "--python", python_version, "--seed", str(venv)],
            cwd=work,
            env=isolated_env,
        )
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
            env=isolated_env,
        )

        batch_outputs = tuple(work / f"batch-{index}" for index in range(3))
        comparison_outputs = tuple(work / f"comparison-{index}" for index in range(3))
        for batch_output, comparison_output in zip(batch_outputs, comparison_outputs, strict=True):
            _run(
                [
                    str(python),
                    "-I",
                    "-c",
                    _NETWORK_DENIED_COMPARE,
                    str(batch_input),
                    str(run_plan),
                    str(comparison_plan),
                    str(batch_output),
                    str(comparison_output),
                ],
                cwd=work,
                env=isolated_env,
            )
            _run(
                [str(python), "-I", "-c", _STRICT_RELOAD, str(comparison_output)],
                cwd=work,
                env=isolated_env,
            )

        batch_trees = tuple(_tree(output) for output in batch_outputs)
        comparison_trees = tuple(_tree(output) for output in comparison_outputs)
        if not batch_trees[0] == batch_trees[1] == batch_trees[2]:
            raise RuntimeError("three wheel-only Batch Summary trees are not byte-identical")
        if not comparison_trees[0] == comparison_trees[1] == comparison_trees[2]:
            raise RuntimeError("three wheel-only Comparison trees are not byte-identical")
    print(f"M3 wheels verified on CPython {python_version}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-wheel", type=Path, required=True)
    parser.add_argument("--companion-wheel", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=ROOT / "examples/m3-offline")
    parser.add_argument(
        "--python", action="append", choices=("3.12", "3.13"), dest="python_versions"
    )
    args = parser.parse_args()

    core_wheel = args.core_wheel.resolve(strict=True)
    companion_wheel = args.companion_wheel.resolve(strict=True)
    fixture = args.fixture.resolve(strict=True)
    versions = tuple(args.python_versions) if args.python_versions else ("3.12", "3.13")
    with tempfile.TemporaryDirectory(prefix="cernora-m3-wheel-inputs-") as directory:
        snapshot_root = Path(directory)
        core_snapshot = snapshot_root / core_wheel.name
        companion_snapshot = snapshot_root / companion_wheel.name
        core_snapshot.write_bytes(read_regular_file_bytes(core_wheel, maximum=None))
        companion_snapshot.write_bytes(read_regular_file_bytes(companion_wheel, maximum=None))
        core_digest = _sha256(core_snapshot)
        companion_digest = _sha256(companion_snapshot)
        if core_digest != EXPECTED_CORE_WHEEL_SHA256:
            raise RuntimeError("Core 0.1.4 wheel digest does not match the accepted candidate")
        for version in versions:
            if _sha256(core_snapshot) != core_digest or (
                _sha256(companion_snapshot) != companion_digest
            ):
                raise RuntimeError("wheel input snapshot changed between Python acceptance runs")
            _verify_python(
                python_version=version,
                core_wheel=core_snapshot,
                companion_wheel=companion_snapshot,
                fixture=fixture,
            )
        if _sha256(core_snapshot) != core_digest or _sha256(companion_snapshot) != companion_digest:
            raise RuntimeError("wheel input snapshot changed during acceptance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
