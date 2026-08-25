"""Verify the public Cernora wheel in clean source-isolated CPython projects."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cernora_reference_workflow.freeze import AttemptCapture, freeze_attempt
from cernora_reference_workflow.spec_builder import build_tiny_calculator_spec
from cernora_reference_workflow.test_runner import (
    TEST_IDS,
    RawTestResults,
    ResourceReceipt,
    canonical_raw_test_output,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WHEEL_SHA256 = "01de19a484172cc8e3940792b90de04683da600320d154fff18b0a717738a2df"


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _passing_export(work: Path) -> tuple[Path, Path]:
    task = ROOT / "tasks/tiny-calculator-v1"
    spec = build_tiny_calculator_spec(ROOT)
    spec_path = work / "experiment-spec.json"
    spec_path.write_bytes(spec.canonical_bytes())
    candidate = work / "candidate"
    (candidate / "src").mkdir(parents=True)
    shutil.copyfile(task / "environment/pyproject.toml", candidate / "pyproject.toml")
    (candidate / "src/calc.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    expected = (-1, -5, 5, 4)
    categories = ("fail-to-pass", "fail-to-pass", "pass-to-pass", "pass-to-pass")
    raw = RawTestResults.model_validate(
        {
            "schema_version": "cernora.reference.test-results/v1",
            "termination": "exited",
            "tests": [
                {
                    "test_id": test_id,
                    "category": categories[index],
                    "passed": True,
                    "expected": expected[index],
                    "actual": expected[index],
                    "error": None,
                }
                for index, test_id in enumerate(TEST_IDS)
            ],
            "runner_error": None,
        }
    )
    stdout = work / "stdout.txt"
    stderr = work / "stderr.txt"
    stdout.write_bytes(canonical_raw_test_output(raw))
    stderr.write_bytes(b"")
    export = work / "completed-export"
    freeze_attempt(
        spec=spec,
        capture=AttemptCapture(
            source_trial_id="public-wheel-conformance",
            candidate_root=candidate,
            test_stdout=stdout,
            test_stderr=stderr,
            test_exit_code=0,
            resource_receipt=ResourceReceipt(
                schema_version="cernora.reference.resource-receipt/v1",
                duration_milliseconds=None,
                peak_memory_bytes=None,
                cpu_milliseconds=None,
            ),
        ),
        baseline_root=task / "environment",
        test_plan_path=task / "tests/test-plan.json",
        requested_state="completed",
        predecessor_attempt_id=None,
        destination=export,
    )
    return spec_path, export


def _verify_python(python_version: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f"cernora-public-wheel-{python_version}-") as directory:
        work = Path(directory)
        spec_path, export = _passing_export(work)
        wheel_output = work / "companion-wheel"
        _run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheel_output)],
            cwd=ROOT,
        )
        companion_wheels = tuple(wheel_output.glob("cernora_reference_workflow-*.whl"))
        if len(companion_wheels) != 1:
            raise RuntimeError("companion wheel build did not produce exactly one wheel")

        venv = work / "venv"
        _run(["uv", "venv", "--python", python_version, "--seed", str(venv)], cwd=work)
        python = venv / "bin/python"
        wheelhouse = work / "wheelhouse"
        wheelhouse.mkdir()
        _run(
            [
                str(python),
                "-m",
                "pip",
                "download",
                "--dest",
                str(wheelhouse),
                str(companion_wheels[0]),
            ],
            cwd=work,
        )
        cernora_wheels = tuple(wheelhouse.glob("cernora-0.1.2-*.whl"))
        if len(cernora_wheels) != 1 or _sha256(cernora_wheels[0]) != EXPECTED_WHEEL_SHA256:
            raise RuntimeError("downloaded public Cernora wheel digest mismatch")
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                str(companion_wheels[0]),
            ],
            cwd=work,
        )
        output = work / "isolated-evaluation"
        code = """
import importlib.metadata
import importlib.util
import sys
from pathlib import Path
import cernora
import cernora_reference_workflow
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.offline import evaluate_frozen_export
from cernora_reference_workflow.spec_builder import RUNTIME_CONFIGURATION_SHA256

spec = ExperimentSpec.from_file(Path(sys.argv[1]))
result = evaluate_frozen_export(
    spec=spec,
    export_root=Path(sys.argv[2]),
    output_root=Path(sys.argv[3]),
)
assert result.receipt.case_outcome == "pass"
assert importlib.metadata.version("cernora") == "0.1.2"
assert importlib.util.find_spec("harbor") is None
assert len(RUNTIME_CONFIGURATION_SHA256) == 64
assert "site-packages" in str(Path(cernora.__file__).resolve())
assert "site-packages" in str(Path(cernora_reference_workflow.__file__).resolve())
"""
        isolated_env = os.environ.copy()
        isolated_env.pop("PYTHONPATH", None)
        _run(
            [
                str(python),
                "-I",
                "-c",
                code,
                str(spec_path),
                str(export),
                str(output),
            ],
            cwd=work,
            env=isolated_env,
        )
    print(
        f"public wheel verified on CPython {python_version}: "
        f"cernora==0.1.2 sha256={EXPECTED_WHEEL_SHA256}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--python",
        action="append",
        choices=("3.12", "3.13"),
        dest="python_versions",
        help="verify only this CPython minor; repeat to select both",
    )
    args = parser.parse_args()
    baseline = json.loads((ROOT / "baselines/cernora-0.1.2.json").read_text(encoding="utf-8"))
    if baseline.get("wheel_sha256") != EXPECTED_WHEEL_SHA256:
        raise RuntimeError("recorded Cernora wheel baseline digest is incorrect")
    versions = tuple(args.python_versions) if args.python_versions else ("3.12", "3.13")
    for python_version in versions:
        _verify_python(python_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
