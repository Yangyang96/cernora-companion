from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from cernora_reference_workflow.candidate_development import (
    CandidateDevelopmentRecord,
    candidate_continuity_violations,
)
from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.controlled_study import AwaitingAcceptanceOutcome
from cernora_reference_workflow.heldout_seal import seal_heldout_cases

if TYPE_CHECKING:
    from create_p4_study_materialization import P4StudyAssembly

REPOSITORY = Path(__file__).resolve().parents[2]
VISIBLE_ROOT = REPOSITORY / "examples" / "priority4-development-pilot"
PRIOR_RECORD = REPOSITORY / "preparations" / "p4-candidate-development-csv-quoted" / "record.json"
CANDIDATE_PROMPT = (
    REPOSITORY / "preparations" / "p4-candidate-development-csv-quoted" / "candidate-prompt.json"
)
PILOT_IMAGES = (
    REPOSITORY / "preparations" / "next-priority4-development-pilot-pi-r9" / "images.json"
)
KEY = bytes(range(32))
NONCE = bytes(range(12))


class _MaterializationScript(Protocol):
    def assemble_p4_study(self, **kwargs: object) -> P4StudyAssembly: ...

    def run_study_ceremony(self, assembly: P4StudyAssembly, study_root: Path) -> object: ...


def _load_script() -> _MaterializationScript:
    location = REPOSITORY / "scripts" / "create_p4_study_materialization.py"
    spec = importlib.util.spec_from_file_location("create_p4_study_materialization", location)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_MaterializationScript, module)


class _RevealScript(Protocol):
    def main(self, argv: list[str] | None = None) -> int: ...


def _load_reveal_script() -> _RevealScript:
    location = REPOSITORY / "scripts" / "create_p4_heldout_reveal.py"
    spec = importlib.util.spec_from_file_location("create_p4_heldout_reveal", location)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_RevealScript, module)


def _fake_heldout_cases() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "case_id": f"case-{index:032x}",
            "task": {
                "schema_version": "cernora.reference.heldout-task/v1",
                "language": "python",
                "instruction": f"repair synthetic module {index}",
                "allowed_paths": ["src/module.py"],
                "protected_paths": ["tests/verify.py"],
                "case_version": "1",
            },
            "workspace": {
                "schema_version": "cernora.reference.heldout-workspace/v1",
                "files": [
                    {"path": "src/module.py", "content_utf8": f"VALUE = {index}\n"},
                    {"path": "tests/verify.py", "content_utf8": "assert VALUE == 4\n"},
                ],
            },
            "evaluation": {
                "schema_version": "cernora.reference.heldout-evaluation/v1",
                "command": ["python", "tests/verify.py"],
                "working_directory": ".",
                "timeout_seconds": 60,
                "network": "disabled",
                "expected_exit_code": 0,
                "success_metric": "verifier_exit_zero",
                "failure_codes": [f"mechanism_{index}_v1"],
            },
        }
        for index in range(1, 4)
    )


@pytest.fixture()
def fake_reseal(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A fake sealed commitment whose cases reuse the real ones' shapes."""

    destination = tmp_path / "seal"
    destination.mkdir()
    prior = json.loads(PRIOR_RECORD.read_text())
    manifest, ciphertext = seal_heldout_cases(_fake_heldout_cases(), key=KEY, nonce=NONCE)
    manifest_path = destination / "manifest.json"
    ciphertext_path = destination / "ciphertext.bin"
    key_path = destination / "reveal.key"
    manifest_path.write_bytes(manifest.canonical_bytes())
    ciphertext_path.write_bytes(ciphertext)
    key_path.write_bytes(KEY)
    prior_path = destination / "prior-copy.json"
    prior_path.write_bytes(PRIOR_RECORD.read_bytes())
    assert prior["development_id"]  # the real frozen record is loaded below
    return manifest_path, ciphertext_path, key_path


def _reveal_fake_heldout(tmp_path: Path, fake_reseal: tuple[Path, Path, Path]) -> Path:
    manifest_path, ciphertext_path, key_path = fake_reseal
    script = _load_reveal_script()
    output_dir = tmp_path / "revealed"
    status = script.main(
        [
            "--manifest",
            str(manifest_path),
            "--ciphertext",
            str(ciphertext_path),
            "--key",
            str(key_path),
            "--prior-record",
            str(PRIOR_RECORD),
            "--output-dir",
            str(output_dir),
            "--authorize-reveal",
        ]
    )
    assert status == 0, output_dir
    return output_dir


def _study_images_payload(reveal_root: Path) -> dict[str, object]:
    pilot = json.loads(PILOT_IMAGES.read_bytes())
    assert isinstance(pilot, dict)
    base = pilot["build_base_image"]
    entries: list[dict[str, str]] = []
    assert isinstance(pilot["images"], list)
    for entry in pilot["images"]:
        assert isinstance(entry, dict)
        entries.append({"case_id": entry["case_id"], "image": entry["image"]})
    for case_id in sorted(path.stem for path in reveal_root.glob("case-*.json")):
        entries.append(
            {"case_id": case_id, "image": f"cernora-reference/p4-study-{case_id}@sha256:{'b' * 64}"}
        )
    entries.sort(key=lambda item: item["case_id"])
    return {"build_base_image": base, "images": entries}


def _fake_lock_artifacts() -> dict[str, dict[str, object]]:
    return {
        "companion_artifact": {
            "name": "cernora-reference-workflow",
            "version": "0.4.1",
            "kind": "wheel",
            "sha256": "1" * 64,
        },
        "cernora_artifact": {
            "name": "cernora",
            "version": "0.1.4",
            "kind": "wheel",
            "sha256": "2" * 64,
        },
        "runtime_adapter_artifact": {
            "name": "pi-runtime-configuration",
            "version": "1",
            "kind": "policy-bundle",
            "sha256": "3" * 64,
        },
        "harness_artifact": {
            "name": "harbor",
            "version": "0.16.1",
            "kind": "source-tree",
            "sha256": "4" * 64,
        },
    }


def _assemble(
    script: _MaterializationScript, reveal_root: Path, manifest_path: Path
) -> P4StudyAssembly:
    artifacts = _fake_lock_artifacts()
    return script.assemble_p4_study(
        visible_root=VISIBLE_ROOT,
        heldout_task_paths=(
            reveal_root / "case-00000000000000000000000000000001.json",
            reveal_root / "case-00000000000000000000000000000002.json",
            reveal_root / "case-00000000000000000000000000000003.json",
        ),
        images_payload=_study_images_payload(reveal_root),
        prior_record_path=PRIOR_RECORD,
        candidate_prompt_path=CANDIDATE_PROMPT,
        manifest_path=manifest_path,
        **artifacts,
    )


def test_assemble_binds_the_twelve_case_study_authorities(
    tmp_path: Path, fake_reseal: tuple[Path, Path, Path]
) -> None:
    manifest_path, _, _ = fake_reseal
    reveal_root = _reveal_fake_heldout(tmp_path, fake_reseal)
    script = _load_script()
    assembly = _assemble(script, reveal_root, manifest_path)

    assert assembly.run_plan.planned_trial_count == 72
    assert assembly.run_plan.execution.max_attempt_count == 144
    assert assembly.run_plan.execution.max_total_wall_time_seconds == 160_000
    assert assembly.run_plan.companion_version == "0.4.2"
    assert len(assembly.binding.ordered_trial_slot_ids) == 72
    assert len(assembly.intent.cases) == 12
    assert assembly.intent.heldout_commitment.case_count == 3
    assert assembly.intent.max_attempt_count == 144
    assert assembly.intent.max_wall_seconds == 160_000

    prior_payload = json.loads(PRIOR_RECORD.read_bytes())
    prior = CandidateDevelopmentRecord.model_validate(prior_payload)
    assert candidate_continuity_violations(prior, assembly.development) == ()
    assert assembly.development.development_id != prior.development_id

    heldout_ids = {item.case_id for item in assembly.intent.cases if item.split == "held-out"}
    assert heldout_ids == {
        "case-00000000000000000000000000000001",
        "case-00000000000000000000000000000002",
        "case-00000000000000000000000000000003",
    }
    descriptive = set(assembly.protocol.claims.descriptive_case_ids)
    guardrail = set(assembly.protocol.claims.guardrail_case_ids)
    primary = set(assembly.protocol.claims.primary_case_ids)
    assert len(descriptive) == 6 and len(guardrail) == 3 and len(primary) == 3
    assert primary == heldout_ids

    heldout_entries = [
        item.model_dump(mode="json") for item in assembly.intent.cases if item.split == "held-out"
    ]
    assert assembly.intent.heldout_commitment.case_commitment_root_sha256 == sha256_bytes(
        canonical_json_bytes(heldout_entries)
    )
    assert assembly.intent.heldout_commitment.manifest_sha256 == sha256_bytes(
        Path(manifest_path).read_bytes()
    )


def test_assemble_rejects_off_matrix_bounds(
    tmp_path: Path,
    fake_reseal: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _, _ = fake_reseal
    reveal_root = _reveal_fake_heldout(tmp_path, fake_reseal)
    script = _load_script()
    monkeypatch.setattr(script, "MAX_WALL_SECONDS", 160_001, raising=False)
    artifacts = _fake_lock_artifacts()
    with pytest.raises(ValueError, match="P4 study execution matrix"):
        script.assemble_p4_study(
            visible_root=VISIBLE_ROOT,
            heldout_task_paths=tuple(sorted(reveal_root.glob("case-*.json"))),
            images_payload=_study_images_payload(reveal_root),
            prior_record_path=PRIOR_RECORD,
            candidate_prompt_path=CANDIDATE_PROMPT,
            manifest_path=manifest_path,
            **artifacts,
        )


def test_assemble_rejects_drifted_candidate_prompt(
    tmp_path: Path, fake_reseal: tuple[Path, Path, Path]
) -> None:
    manifest_path, _, _ = fake_reseal
    reveal_root = _reveal_fake_heldout(tmp_path, fake_reseal)
    drifted = tmp_path / "candidate-prompt.json"
    payload = json.loads(CANDIDATE_PROMPT.read_bytes())
    assert isinstance(payload, dict) and isinstance(payload["payload"], dict)
    payload["payload"]["text"] = "drifted treatment text"
    drifted.write_bytes(canonical_json_bytes(payload))

    script = _load_script()
    artifacts = _fake_lock_artifacts()
    with pytest.raises(ValueError, match="authority source digest"):
        script.assemble_p4_study(
            visible_root=VISIBLE_ROOT,
            heldout_task_paths=tuple(sorted(reveal_root.glob("case-*.json"))),
            images_payload=_study_images_payload(reveal_root),
            prior_record_path=PRIOR_RECORD,
            candidate_prompt_path=drifted,
            manifest_path=manifest_path,
            **artifacts,
        )


def test_run_ceremony_stops_at_awaiting_acceptance(
    tmp_path: Path, fake_reseal: tuple[Path, Path, Path]
) -> None:
    manifest_path, _, _ = fake_reseal
    reveal_root = _reveal_fake_heldout(tmp_path, fake_reseal)
    script = _load_script()
    assembly = _assemble(script, reveal_root, manifest_path)

    custody_parent = REPOSITORY / ".agent" / "test-controlled-study"
    custody_parent.mkdir(parents=True, exist_ok=True)
    study_root = custody_parent / f"materialize-{tmp_path.name}"
    shutil.rmtree(study_root, ignore_errors=True)
    try:
        outcome = script.run_study_ceremony(assembly, study_root)
        assert isinstance(outcome, AwaitingAcceptanceOutcome)
        assert (study_root / "ledger").is_dir()
    finally:
        shutil.rmtree(study_root, ignore_errors=True)
