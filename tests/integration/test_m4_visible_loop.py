from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

VISIBLE_ROOT = Path(__file__).parents[2] / "examples" / "m4-visible"


def test_six_visible_cases_are_substantive_frozen_python_repairs() -> None:
    cases = sorted(VISIBLE_ROOT.glob("*/case.json"))
    assert len(cases) == 6
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in cases]
    assert [item["split_id"] for item in manifests].count("development") == 3
    assert [item["split_id"] for item in manifests].count("regression") == 3
    assert len({item["case_id"] for item in manifests}) == 6
    assert len({item["failure_code"] for item in manifests}) == 6

    for manifest_path in cases:
        root = manifest_path.parent
        baseline = subprocess.run(
            [sys.executable, str(root / "verify.py"), str(root / "baseline.py")],
            check=False,
            capture_output=True,
            timeout=5,
        )
        solution = subprocess.run(
            [sys.executable, str(root / "verify.py"), str(root / "solution.py")],
            check=False,
            capture_output=True,
            timeout=5,
        )
        assert baseline.returncode != 0, manifest_path.parent.name
        assert solution.returncode == 0, (manifest_path.parent.name, solution.stderr.decode())
