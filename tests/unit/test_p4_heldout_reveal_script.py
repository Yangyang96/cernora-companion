from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

from cernora_reference_workflow.candidate_development import freeze_candidate_development
from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.heldout_seal import (
    HeldoutManifest,
    HeldoutRevealReceipt,
    seal_heldout_cases,
)

REPOSITORY = Path(__file__).resolve().parents[2]
KEY = bytes(range(32))
NONCE = bytes(range(12))


class _RevealScript(Protocol):
    def main(self, argv: list[str] | None = None) -> int: ...


def _load_script() -> _RevealScript:
    location = REPOSITORY / "scripts" / "create_p4_heldout_reveal.py"
    spec = importlib.util.spec_from_file_location("create_p4_heldout_reveal", location)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_RevealScript, module)


def _fake_cases() -> tuple[dict[str, object], ...]:
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


def _prior_record_path(destination: Path) -> Path:
    record = freeze_candidate_development(
        {
            "schema_version": "cernora.reference.candidate-development/v1",
            "baseline": {
                "configuration_id": "baseline",
                "authority_sha256": "6" * 64,
            },
            "candidate": {
                "configuration_id": "candidate",
                "baseline_authority_sha256": "6" * 64,
                "authority_sha256": "c" * 64,
                "treatment_axis": "prompt-instruction",
                "treatment_sha256": "9" * 64,
            },
            "hypothesis": {
                "observed_failure_code": "mechanism_1_v1",
                "mechanism": "The baseline misses the mechanism.",
                "intervention_scope": "Prompt instruction only.",
                "expected_observation": "Fewer held-out failures.",
                "falsifier": "No held-out improvement.",
            },
            "observations": [
                {
                    "observation_id": "agent-failure-001",
                    "case_id": "p4-dev-representative",
                    "split": "development",
                    "source": "agent-pilot",
                    "agent_outcome": "behavioral-failure",
                    "failure_code": "mechanism_1_v1",
                    "evidence_sha256": "d" * 64,
                }
            ],
        }
    )
    path = destination / "prior-record.json"
    path.write_bytes(canonical_json_bytes(record.model_dump(mode="json")))
    return path


def _seal_into(destination: Path) -> tuple[Path, Path, Path]:
    manifest, ciphertext = seal_heldout_cases(_fake_cases(), key=KEY, nonce=NONCE)
    manifest_path = destination / "manifest.json"
    ciphertext_path = destination / "ciphertext.bin"
    manifest_path.write_bytes(manifest.canonical_bytes())
    ciphertext_path.write_bytes(ciphertext)
    key_path = destination / "reveal.key"
    key_path.write_bytes(KEY)
    return manifest_path, ciphertext_path, key_path


def _reveal_command(
    manifest_path: Path,
    ciphertext_path: Path,
    key_path: Path,
    prior_path: Path,
    output_dir: Path,
    *,
    authorized: bool,
) -> list[str]:
    command: list[str] = [
        "--manifest",
        str(manifest_path),
        "--ciphertext",
        str(ciphertext_path),
        "--key",
        str(key_path),
        "--prior-record",
        str(prior_path),
        "--output-dir",
        str(output_dir),
    ]
    if authorized:
        command.append("--authorize-reveal")
    return command


def test_reveal_requires_explicit_authorization(tmp_path: Path) -> None:
    manifest_path, ciphertext_path, key_path = _seal_into(tmp_path)
    prior_path = _prior_record_path(tmp_path)
    script = _load_script()
    output_dir = tmp_path / "revealed"

    status = script.main(
        _reveal_command(
            manifest_path,
            ciphertext_path,
            key_path,
            prior_path,
            output_dir,
            authorized=False,
        )
    )

    assert status == 2
    assert not output_dir.exists()


def test_reveal_publishes_private_task_authorities_and_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, ciphertext_path, key_path = _seal_into(tmp_path)
    prior_path = _prior_record_path(tmp_path)
    script = _load_script()
    output_dir = tmp_path / "revealed"

    status = script.main(
        _reveal_command(
            manifest_path,
            ciphertext_path,
            key_path,
            prior_path,
            output_dir,
            authorized=True,
        )
    )

    assert status == 0
    task_files = sorted(path.name for path in output_dir.glob("case-*.json"))
    assert task_files == [
        "case-00000000000000000000000000000001.json",
        "case-00000000000000000000000000000002.json",
        "case-00000000000000000000000000000003.json",
    ]
    receipt = HeldoutRevealReceipt.from_bytes((output_dir / "reveal-receipt.json").read_bytes())
    prior_bytes = prior_path.read_bytes()
    assert receipt.candidate_freeze_sha256 == sha256_bytes(prior_bytes)
    summary = capsys.readouterr().out
    assert receipt.receipt_id in summary
    assert all(digest not in summary for digest in (KEY.hex(), "reveal.key"))


def test_reveal_fails_closed_on_tampered_ciphertext(tmp_path: Path) -> None:
    manifest_path, ciphertext_path, key_path = _seal_into(tmp_path)
    tampered = bytearray(ciphertext_path.read_bytes())
    tampered[0] ^= 1
    ciphertext_path.write_bytes(bytes(tampered))
    prior_path = _prior_record_path(tmp_path)
    script = _load_script()
    output_dir = tmp_path / "revealed"

    status = script.main(
        _reveal_command(
            manifest_path,
            ciphertext_path,
            key_path,
            prior_path,
            output_dir,
            authorized=True,
        )
    )

    assert status == 1
    assert not output_dir.exists()


def test_revealed_task_files_replay_through_the_public_commitments(
    tmp_path: Path,
) -> None:
    manifest_path, ciphertext_path, key_path = _seal_into(tmp_path)
    prior_path = _prior_record_path(tmp_path)
    script = _load_script()
    output_dir = tmp_path / "revealed"

    status = script.main(
        _reveal_command(
            manifest_path,
            ciphertext_path,
            key_path,
            prior_path,
            output_dir,
            authorized=True,
        )
    )
    assert status == 0

    from cernora_reference_workflow.common import read_regular_file_bytes
    from cernora_reference_workflow.controlled_task import (
        ControlledTaskAuthority,
        reconstructed_revealed_case,
    )

    manifest = HeldoutManifest.from_file(manifest_path)
    commitment_by_case = {item.case_id: item for item in manifest.case_commitments}
    for path in sorted(output_dir.glob("case-*.json")):
        task = ControlledTaskAuthority.from_bytes(read_regular_file_bytes(path))
        commitment = commitment_by_case[task.case.case_id]
        reconstructed = reconstructed_revealed_case(task)
        digest = sha256_bytes(canonical_json_bytes(reconstructed.model_dump(mode="json")))
        assert digest == commitment.plaintext_sha256
