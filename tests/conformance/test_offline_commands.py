from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from cernora_reference_workflow.export import publish_completed_export
from cernora_reference_workflow.offline import evaluate_frozen_export
from cernora_reference_workflow.report import OfflineRebuildAvailable
from cernora_reference_workflow.report_builder import build_run_report
from cernora_reference_workflow.spec_builder import build_tiny_calculator_spec

from ..unit.test_export import materialize_staging

ROOT = Path(__file__).resolve().parents[2]


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_reported_offline_commands_execute_from_a_clean_network_blocked_directory(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "examples").mkdir()
    (checkout / "exports").mkdir()
    shutil.copyfile(ROOT / "scripts/evaluate_frozen.py", checkout / "scripts/evaluate_frozen.py")
    shutil.copyfile(
        ROOT / "examples/tiny-calculator-v1.json",
        checkout / "examples/tiny-calculator-v1.json",
    )

    spec = build_tiny_calculator_spec(ROOT)
    staging = checkout / "exports/staging"
    fields = materialize_staging(staging)
    fields["experiment_id"] = spec.experiment_id
    export = checkout / "exports/success"
    publish_completed_export(staging, export, manifest_fields=fields)
    reference = evaluate_frozen_export(
        spec=spec,
        export_root=export,
        output_root=tmp_path / "reference",
    )
    report = build_run_report(
        spec=spec,
        export_root=export,
        evaluation=reference,
        portable_spec_path="examples/tiny-calculator-v1.json",
        portable_export_path="exports/success",
        portable_bundle_path="rebuild/success/adapted/bundle.json",
        portable_evaluation_path="rebuild/success/evaluated",
    )

    blocker = tmp_path / "network-blocker"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text(
        """import socket

def denied(*args, **kwargs):
    raise RuntimeError('network access is forbidden during frozen evaluation')

socket.create_connection = denied
socket.socket.connect = denied
""",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join((str(blocker), str(ROOT / "src")))

    statuses: list[str] = []
    assert isinstance(report.offline_rebuild, OfflineRebuildAvailable)
    for command in report.offline_rebuild.commands:
        argv = list(command.argv)
        assert argv[:4] == ["uv", "run", "--frozen", "python"]
        completed = subprocess.run(
            [sys.executable, *argv[4:]],
            cwd=checkout,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        statuses.append(completed.stdout)

    assert '"status":"adapted"' in statuses[0]
    assert '"status":"evaluated"' in statuses[1]
    assert '"status":"strictly-reloaded"' in statuses[2]
    assert (
        checkout / "rebuild/success/adapted/bundle.json"
    ).read_bytes() == reference.bundle_path.read_bytes()
    assert _tree(checkout / "rebuild/success/evaluated") == _tree(reference.evaluation_root)
