"""Pin the explicit Codex-era/pi-era Runtime boundary as a tested contract.

Decision (2026-09-03): historical Codex-era artifacts stay frozen exactly as committed —
they are evidence, not migration input. The current strict contracts accept only
pi-era vocabulary, and Codex-era artifacts are verified with the Codex-era revision of
this repository. These tests make the boundary deliberate instead of accidental: a
future change that either silently re-accepts Codex-era vocabulary or rewrites the
historical fixtures will fail here and force the decision to be revisited explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.common import sha256_file
from cernora_reference_workflow.controlled_run_plan import ControlledRunPlanV2
from cernora_reference_workflow.export import REQUIRED_PATHS, ExportError, validate_allowlist

ROOT = Path(__file__).resolve().parents[2]

_HISTORICAL_M3_SHA256 = {
    "batch-input.json": ("362051e4aaa7240918b305158f7c45a9c926a1e892ea79e22379051fed77aa18"),
    "comparison-plan.json": ("64c74427f683c7952499f107e6f922c04d1788af97e802de5e076673288ce3bf"),
    "controlled-run-plan.json": (
        "bcfb21fcd1bfd2cf86df6f67cc7a88ccd6a14fbba0ff6ba6c4e51991da1ec11c"
    ),
}


def test_historical_m3_offline_fixture_is_frozen_in_place() -> None:
    """The trio stays byte-identical: the boundary must never rewrite evidence."""

    for name, digest in _HISTORICAL_M3_SHA256.items():
        path = ROOT / "examples" / "m3-offline" / name
        assert path.is_file(), f"historical evidence removed: {name}"
        assert sha256_file(path) == digest, f"historical evidence mutated: {name}"


def test_current_contracts_reject_the_codex_era_run_plan() -> None:
    with pytest.raises(ValidationError, match="cernora-reference-harbor-pi"):
        ControlledRunPlanV2.from_file(ROOT / "examples/m3-offline/controlled-run-plan.json")


def test_current_export_allowlist_rejects_codex_era_vocabulary() -> None:
    paths = set(REQUIRED_PATHS) | {
        "candidate/files/src/calc.py",
        "runtime/codex-events.jsonl",
    }
    with pytest.raises(ExportError, match="unexpected file: runtime/codex-events.jsonl"):
        validate_allowlist(paths)
