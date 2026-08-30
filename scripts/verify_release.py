"""Compose every deterministic private publication gate except the live tracer."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

from cernora import BatchInput
from generate_license_inventory import build_inventory
from generate_schemas import SchemaModel, schema_bytes
from generate_task_authorities import TASKS, build_task_plans

from cernora_reference_workflow.common import (
    canonical_json_bytes,
    closed_regular_tree,
    read_regular_file_bytes,
)
from cernora_reference_workflow.comparison_plan import ComparisonPlanV1
from cernora_reference_workflow.controlled_experiment_spec import ControlledExperimentSpecV2
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.export import CompletedExportManifest
from cernora_reference_workflow.native_acceptance import build_m1_native_acceptance_plan
from cernora_reference_workflow.profile import create_profile
from cernora_reference_workflow.report import RunReport
from cernora_reference_workflow.run_plan import RunPlan
from cernora_reference_workflow.secrets import scan_bytes
from cernora_reference_workflow.spec_builder import (
    TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    build_tiny_calculator_spec,
    build_tiny_calculator_v2_spec,
)

EXPECTED_CORE_M3_WHEEL_SHA256 = "4ef10a5eb2f9961943883576ab81bc97ce32d2f3f8a88cb9679d5c51c81e368d"
ROOT = Path(__file__).resolve().parents[1]


def _main_checkout_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = ROOT / common
    common = common.resolve()
    if common.name != ".git" or not common.is_dir():
        raise RuntimeError("cannot resolve the Companion main checkout from Git common-dir")
    return common.parent


def _verify_m4_release_surface() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["version"] != "0.4.0":
        raise RuntimeError("Priority 4 M4 requires companion version 0.4.0")
    if project["project"].get("scripts", {}).get("experiment") != (
        "cernora_reference_workflow.cli:main"
    ):
        raise RuntimeError("Priority 4 M4 experiment CLI entry point is missing")

    dependencies = project["project"].get("dependencies", [])
    if "cernora==0.1.4" not in dependencies:
        raise RuntimeError("Priority 4 M4 requires the exact Core 0.1.4 candidate")

    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    local = [item for item in lock["package"] if item["name"] == "cernora-reference-workflow"]
    if len(local) != 1 or local[0]["version"] != "0.4.0":
        raise RuntimeError("uv.lock does not bind companion version 0.4.0")
    core = [item for item in lock["package"] if item["name"] == "cernora"]
    if len(core) != 1 or core[0]["version"] != "0.1.4":
        raise RuntimeError("uv.lock does not bind Core version 0.1.4")
    source = core[0].get("source")
    if source != {"registry": "../cernora/dist"}:
        raise RuntimeError("uv.lock must use the stable sibling Core candidate wheelhouse")
    wheels = core[0].get("wheels")
    if wheels != [{"path": "cernora-0.1.4-py3-none-any.whl"}]:
        raise RuntimeError("uv.lock does not bind the Core 0.1.4 wheel filename")
    core_wheel = _main_checkout_root().parent / "cernora/dist/cernora-0.1.4-py3-none-any.whl"
    digest = hashlib.sha256(read_regular_file_bytes(core_wheel, maximum=None)).hexdigest()
    if digest != EXPECTED_CORE_M3_WHEEL_SHA256:
        raise RuntimeError("the sibling Core 0.1.4 wheel digest is not accepted")
    required = (
        ROOT / "docs/batch-summary.md",
        ROOT / "docs/controlled-comparison.md",
        ROOT / "docs/controlled-study.md",
        ROOT / "docs/repeat-runner.md",
        ROOT / "examples/m3-offline/batch-input.json",
        ROOT / "examples/m3-offline/comparison-plan.json",
        ROOT / "examples/m3-offline/controlled-run-plan.json",
        ROOT / "schemas/comparison-plan-v1.schema.json",
        ROOT / "schemas/controlled-experiment-spec-v2.schema.json",
        ROOT / "schemas/controlled-run-plan-v2.schema.json",
        ROOT / "schemas/run-plan-v1.schema.json",
        ROOT / "scripts/verify_batch_wheels.py",
        ROOT / "scripts/verify_comparison_wheels.py",
        ROOT / "src/cernora_reference_workflow/batch_summary.py",
        ROOT / "src/cernora_reference_workflow/comparison_input.py",
        ROOT / "src/cernora_reference_workflow/comparison_plan.py",
        ROOT / "src/cernora_reference_workflow/controlled_experiment_spec.py",
        ROOT / "src/cernora_reference_workflow/controlled_run_plan.py",
        ROOT / "tests/conformance/test_repeat_runner.py",
        ROOT / "tests/unit/test_batch_summary.py",
        ROOT / "tests/unit/test_comparison_input.py",
        ROOT / "tests/unit/test_comparison_plan.py",
        ROOT / "tests/unit/test_controlled_experiment_spec.py",
        ROOT / "tests/unit/test_controlled_run_plan.py",
    )
    if any(not path.is_file() or path.is_symlink() or not path.read_bytes() for path in required):
        raise RuntimeError("Priority 4 M4 release surface is incomplete")


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def _repository_files() -> tuple[Path, ...]:
    if not (ROOT / ".git").exists():
        return tuple(closed_regular_tree(ROOT).values())
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    relatives = tuple(item for item in result.stdout.split(b"\0") if item)
    files: list[Path] = []
    for encoded in relatives:
        relative = encoded.decode("utf-8")
        path = ROOT / relative
        if path.is_file():
            files.append(path)
    return tuple(sorted(files))


def _require_secret_free_files(files: tuple[Path, ...]) -> None:
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        findings = scan_bytes(relative, path.read_bytes())
        if findings:
            kinds = ",".join(item.kind for item in findings)
            raise RuntimeError(f"repository secret scan failed: {relative}:{kinds}")


def _verify_generated_artifacts() -> None:
    examples = {
        ROOT / "examples/tiny-calculator-v1.json": build_tiny_calculator_spec(ROOT),
        ROOT / "examples/tiny-calculator-v1-timeout.json": build_tiny_calculator_spec(
            ROOT,
            timeout_seconds=3,
            agent_timeout_multiplier=TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
        ),
        ROOT / "examples/tiny-calculator-v1-interruption.json": build_tiny_calculator_spec(
            ROOT, operator_interrupt=True
        ),
        ROOT / "examples/tiny-calculator-v2.json": build_tiny_calculator_v2_spec(ROOT),
        ROOT / "examples/tiny-calculator-v2-timeout.json": build_tiny_calculator_v2_spec(
            ROOT,
            timeout_seconds=3,
            agent_timeout_multiplier=TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
        ),
    }
    for example, expected in examples.items():
        if example.read_bytes() != expected.canonical_bytes():
            raise RuntimeError(f"checked-in ExperimentSpec example is stale: {example.name}")
    native_plan = ROOT / "examples/priority4-m1-native-acceptance.json"
    if native_plan.read_bytes() != build_m1_native_acceptance_plan(ROOT).canonical_bytes():
        raise RuntimeError("checked-in M1 native acceptance RunPlan is stale")
    for task_id, plan in build_task_plans().items():
        encoded = canonical_json_bytes(plan.model_dump(mode="json"))
        task_plan = ROOT / "tasks" / task_id / "tests/test-plan.json"
        profile_plan = (
            ROOT
            / "profiles/cernora-reference-coding-v1/resources"
            / TASKS[task_id]["profile_fixture"]
        )
        if task_plan.read_bytes() != encoded or profile_plan.read_bytes() != encoded:
            raise RuntimeError(f"checked-in Test Plan authority is stale: {task_id}")
    profile = create_profile().authority
    profile_bytes = canonical_json_bytes(profile.model_dump(mode="json", exclude_none=False))
    if (ROOT / "profiles/cernora-reference-coding-v1/profile.json").read_bytes() != profile_bytes:
        raise RuntimeError("checked-in Profile authority is stale")
    schema_models: dict[str, SchemaModel] = {
        "experiment-spec-v1.schema.json": ExperimentSpec,
        "completed-export-v1.schema.json": CompletedExportManifest,
        "run-report-v1.schema.json": RunReport,
        "run-plan-v1.schema.json": RunPlan,
        "controlled-experiment-spec-v2.schema.json": ControlledExperimentSpecV2,
        "controlled-run-plan-v2.schema.json": ControlledRunPlanV2,
        "comparison-plan-v1.schema.json": ComparisonPlanV1,
    }
    for name, model in schema_models.items():
        if (ROOT / "schemas" / name).read_bytes() != schema_bytes(name, model):
            raise RuntimeError(f"checked-in JSON Schema is stale: {name}")
    fixture = ROOT / "examples/m3-offline"
    run_plan = ControlledRunPlanV2.from_file(fixture / "controlled-run-plan.json")
    comparison_plan = ComparisonPlanV1.from_file(fixture / "comparison-plan.json")
    comparison_plan.validate_run_plan(run_plan)
    batch_path = fixture / "batch-input.json"
    batch_input = BatchInput.model_validate_json(batch_path.read_bytes())
    if batch_path.read_bytes() != canonical_json_bytes(batch_input.model_dump(mode="json")):
        raise RuntimeError("checked-in M3 BatchInput fixture is not canonical")
    if batch_input.run_plan_id != run_plan.run_plan_id:
        raise RuntimeError("checked-in M3 BatchInput fixture does not bind its V2 RunPlan")
    inventory = ROOT / "docs/license-inventory.json"
    if inventory.read_bytes() != canonical_json_bytes(build_inventory()) + b"\n":
        raise RuntimeError("checked-in license inventory is stale")
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    packages = payload.get("packages")
    if not isinstance(packages, list):
        raise RuntimeError("license inventory package list is malformed")
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {
        item["name"] for item in lock["package"] if item["name"] != "cernora-reference-workflow"
    }
    inventoried = {item["name"] for item in packages}
    if inventoried != locked:
        raise RuntimeError("license inventory does not cover the complete lockfile")
    if any(not item.get("license") or item["license"].startswith("not-") for item in packages):
        raise RuntimeError("license inventory contains an unresolved license")
    harbor = [item for item in packages if item["name"] == "harbor"]
    if len(harbor) != 1 or harbor[0]["version"] != "0.16.1" or harbor[0]["license"] != "Apache-2.0":
        raise RuntimeError("Harbor Apache-2.0 license inventory record is invalid")


def _scan_archive_member(name: str, data: bytes) -> None:
    findings = scan_bytes(name, data)
    if findings:
        kinds = ",".join(item.kind for item in findings)
        raise RuntimeError(f"built artifact secret scan failed: {name}:{kinds}")


def _verify_built_artifacts() -> None:
    with tempfile.TemporaryDirectory(prefix="cernora-release-build-") as directory:
        output = Path(directory)
        _run(["uv", "build", "--offline", "--out-dir", str(output)])
        wheels = tuple(output.glob("*.whl"))
        sdists = tuple(output.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise RuntimeError("release build did not produce exactly one wheel and one sdist")
        with zipfile.ZipFile(wheels[0]) as archive:
            for name in archive.namelist():
                if not name.endswith("/"):
                    _scan_archive_member(name, archive.read(name))
        with tarfile.open(sdists[0], mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.isfile():
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise RuntimeError(f"cannot inspect sdist member: {member.name}")
                    _scan_archive_member(member.name, handle.read())


def _verify_no_cernora_internal_imports(files: tuple[Path, ...]) -> None:
    for path in files:
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("from cernora.") or stripped.startswith("import cernora."):
                relative = path.relative_to(ROOT).as_posix()
                raise RuntimeError(f"Cernora internal import is prohibited: {relative}")


def main() -> int:
    files = _repository_files()
    _require_secret_free_files(files)
    _verify_no_cernora_internal_imports(files)
    _verify_m4_release_surface()
    _verify_generated_artifacts()
    _run([sys.executable, "-m", "pytest", "-q"])
    _run([sys.executable, "-m", "ruff", "check", "."])
    _run([sys.executable, "-m", "ruff", "format", "--check", "."])
    _run([sys.executable, "-m", "mypy"])
    _verify_built_artifacts()
    print(
        json.dumps(
            {
                "artifact_build": "passed",
                "license_inventory": "passed",
                "quality_gates": "passed",
                "controlled_comparison_m3_source": "passed",
                "controlled_comparison_m3_wheel_only": "separate-required",
                "batch_summary_m2": "preserved",
                "repeat_runner_m1": "preserved",
                "repository_secret_scan": "passed",
                "scope": "offline-private-publication-gate",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
