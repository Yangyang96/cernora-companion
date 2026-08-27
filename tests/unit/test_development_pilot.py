from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cernora import reload_batch_summary_package

from cernora_reference_workflow.common import ContractError, canonical_json_bytes, load_json_bytes
from cernora_reference_workflow.controlled_evaluation import RepairResultRecord
from cernora_reference_workflow.development_pilot import (
    DEVELOPMENT_CASE_IDS,
    _minimal_offline_environment,
    create_candidate_freeze,
    create_development_pilot,
)
from cernora_reference_workflow.improvement_loop import CandidateFreeze

ROOT = Path(__file__).resolve().parents[2]
VISIBLE = ROOT / "examples" / "m4-visible"
MANIFEST = ROOT / "examples" / "m4-heldout-sealed" / "manifest.json"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _repair_results(pilot_root: Path) -> tuple[RepairResultRecord, ...]:
    package = reload_batch_summary_package(pilot_root)
    results: list[RepairResultRecord] = []
    for trial in package.batch_input.trials:
        evaluation = trial.attempts[-1].evaluation
        assert evaluation is not None
        raw = evaluation.file_payloads()["source-import/artifacts/evidence/repair-result.json"]
        payload = load_json_bytes(raw)
        assert isinstance(payload, dict)
        results.append(RepairResultRecord.model_validate(payload))
    return tuple(results)


def test_real_development_pilot_and_freeze_are_strict_and_byte_identical(
    tmp_path: Path,
) -> None:
    trees: list[dict[str, bytes]] = []
    freeze_bytes: list[bytes] = []
    for index in range(2):
        pilot_root = tmp_path / f"pilot-{index}"
        freeze_path = tmp_path / f"freeze-{index}.json"
        pilot, freeze = create_candidate_freeze(
            visible_root=VISIBLE,
            public_manifest=MANIFEST,
            pilot_output=pilot_root,
            freeze_output=freeze_path,
        )
        assert reload_batch_summary_package(pilot_root) == pilot
        assert CandidateFreeze.from_file(freeze_path) == freeze
        assert tuple(item.case_id for item in pilot.batch_input.trials) == DEVELOPMENT_CASE_IDS
        assert {item.configuration_id for item in pilot.batch_input.trials} == {"baseline"}
        assert pilot.batch_input.companion_version == "0.4.0-offline-pilot"
        assert all(len(item.attempts) == 1 for item in pilot.batch_input.trials)
        assert all(item.attempts[0].lifecycle is None for item in pilot.batch_input.trials)
        assert freeze.leading_failure.code == "interval_boundary_v1"
        assert freeze.leading_failure.count == 1
        assert freeze.candidate_prompt_authority.payload == {
            "selected_failure": {
                "code": "interval_boundary_v1",
                "profile_id": "cernora-controlled-repair-v1",
                "profile_version": "1.0.0",
            },
            "text": (
                "When merging intervals, treat a shared endpoint as overlap and merge the "
                "touching intervals."
            ),
        }
        trees.append(_tree_bytes(pilot_root))
        freeze_bytes.append(freeze_path.read_bytes())

    assert trees[0] == trees[1]
    assert freeze_bytes[0] == freeze_bytes[1]
    authoritative = b"".join(trees[0].values()) + freeze_bytes[0]
    assert str(tmp_path).encode() not in authoritative
    assert str(VISIBLE).encode() not in authoritative
    results = _repair_results(tmp_path / "pilot-0")
    assert tuple(item.case_id for item in results) == DEVELOPMENT_CASE_IDS
    assert all(item.termination == "exited" for item in results)
    assert all(item.changed_paths == () for item in results)
    assert all(item.protected_path_receipt.unchanged for item in results)
    assert tuple(item.failure_codes for item in results) == (
        ("ledger_cents_sign_v1",),
        ("interval_boundary_v1",),
        ("query_repeated_escape_v1",),
    )


def test_verifier_environment_is_explicit_minimal_and_value_free() -> None:
    environment = _minimal_offline_environment()
    assert environment == {
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "TZ": "UTC",
    }
    forbidden = ("proxy", "auth", "token", "secret", "key", "home", "path")
    assert not any(marker in name.lower() for name in environment for marker in forbidden)


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_wrong_development_case_set_fails_closed(tmp_path: Path, mutation: str) -> None:
    visible = tmp_path / "visible"
    shutil.copytree(VISIBLE, visible)
    if mutation == "missing":
        shutil.rmtree(visible / DEVELOPMENT_CASE_IDS[0])
    else:
        extra = visible / "dev-extra"
        shutil.copytree(visible / DEVELOPMENT_CASE_IDS[0], extra)
        manifest = json.loads((extra / "case.json").read_text())
        manifest["case_id"] = "dev-extra"
        (extra / "case.json").write_bytes(canonical_json_bytes(manifest))
    output = tmp_path / "pilot"
    with pytest.raises(ContractError, match="exact development Case set"):
        create_development_pilot(visible_root=visible, output=output)
    assert not output.exists()


def test_all_passing_development_baselines_fail_closed(tmp_path: Path) -> None:
    visible = tmp_path / "visible"
    shutil.copytree(VISIBLE, visible)
    for case_id in DEVELOPMENT_CASE_IDS:
        case_root = visible / case_id
        (case_root / "baseline.py").write_bytes((case_root / "solution.py").read_bytes())
    output = tmp_path / "pilot"
    with pytest.raises(ContractError, match="no usable versioned failure"):
        create_development_pilot(visible_root=visible, output=output)
    assert not output.exists()


def test_output_collisions_fail_closed_without_replacement(tmp_path: Path) -> None:
    pilot_path = tmp_path / "pilot"
    create_development_pilot(visible_root=VISIBLE, output=pilot_path)
    original_pilot = _tree_bytes(pilot_path)
    with pytest.raises(ContractError, match="already exists"):
        create_development_pilot(visible_root=VISIBLE, output=pilot_path)
    assert _tree_bytes(pilot_path) == original_pilot

    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_bytes(b"occupied")
    with pytest.raises(ContractError, match="already exists"):
        create_candidate_freeze(
            visible_root=VISIBLE,
            public_manifest=MANIFEST,
            pilot_output=tmp_path / "unused-pilot",
            freeze_output=freeze_path,
        )
    assert freeze_path.read_bytes() == b"occupied"
    assert not (tmp_path / "unused-pilot").exists()


def test_cli_prints_only_stable_public_identities_and_has_value_free_errors(
    tmp_path: Path,
) -> None:
    script = ROOT / "scripts" / "create_m4_candidate_freeze.py"
    environment = {"PYTHONPATH": str(ROOT / "src")}
    command = (
        sys.executable,
        str(script),
        "--visible-root",
        str(VISIBLE),
        "--public-manifest",
        str(MANIFEST),
        "--pilot-output",
        str(tmp_path / "pilot"),
        "--freeze-output",
        str(tmp_path / "freeze.json"),
    )
    completed = subprocess.run(command, env=environment, capture_output=True, check=False)
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert set(payload) == {
        "candidate_freeze_id",
        "candidate_freeze_sha256",
        "pilot_id",
        "summary_id",
        "summary_sha256",
    }
    assert completed.stderr == b""
    assert str(tmp_path).encode() not in completed.stdout

    collision = subprocess.run(command, env=environment, capture_output=True, check=False)
    assert collision.returncode == 1
    assert collision.stdout == b""
    assert collision.stderr == b"error: CandidateFreeze generation failed\n"
    assert str(tmp_path).encode() not in collision.stderr
