from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest
from pydantic import ValidationError

from cernora_reference_workflow.common import canonical_content_id, sha256_bytes
from cernora_reference_workflow.heldout_seal import (
    HeldoutArchive,
    HeldoutArchiveCase,
    HeldoutManifest,
    HeldoutRevealReceipt,
    HeldoutSealError,
    _aad_bytes,
    reveal_heldout_archive,
    seal_heldout_cases,
    verify_revealed_archive,
)

KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))
NONCE = bytes(range(12))
CANDIDATE_ID = "candidate-freeze-001"
CANDIDATE_SHA256 = "a" * 64


class _SealScript(Protocol):
    def main(self, argv: list[str] | None = None) -> int: ...


def _cases() -> tuple[dict[str, object], ...]:
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
                "failure_codes": ["wrong_value_v1"],
            },
        }
        for index in range(1, 4)
    )


def _sealed() -> tuple[HeldoutManifest, bytes, HeldoutRevealReceipt]:
    manifest, ciphertext = seal_heldout_cases(_cases(), key=KEY, nonce=NONCE)
    _, receipt = reveal_heldout_archive(
        manifest,
        ciphertext,
        key=KEY,
        candidate_freeze_id=CANDIDATE_ID,
        candidate_freeze_sha256=CANDIDATE_SHA256,
    )
    return manifest, ciphertext, receipt


def _reidentify_manifest(payload: dict[str, object]) -> HeldoutManifest:
    payload["aad_sha256"] = sha256_bytes(_aad_bytes(payload))
    payload["manifest_id"] = canonical_content_id(payload, excluded=frozenset({"manifest_id"}))
    return HeldoutManifest.model_validate(payload)


def _canonical_dummy_archive(path: Path) -> Path:
    archive = HeldoutArchive(
        schema_version="cernora.reference.heldout-archive/v1",
        cases=tuple(HeldoutArchiveCase.model_validate(case) for case in _cases()),
    )
    path.write_bytes(archive.canonical_bytes())
    return path


def _load_seal_script() -> tuple[_SealScript, ModuleType]:
    script = Path(__file__).resolve().parents[2] / "scripts/create_m4_heldout_seal.py"
    specification = importlib.util.spec_from_file_location("m4_heldout_seal_script", script)
    if specification is None or specification.loader is None:
        raise AssertionError("could not load held-out seal script")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return cast(_SealScript, module), module


def _representative_worktrees(tmp_path: Path) -> tuple[Path, ...]:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", str(main)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            "test fixture",
        ],
        cwd=main,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(linked)],
        cwd=main,
        check=True,
        capture_output=True,
    )
    assert (main / ".git").is_dir()
    assert (linked / ".git").is_file()
    return main, linked


def test_seal_and_reveal_are_canonical_and_deterministic() -> None:
    manifest, ciphertext, receipt = _sealed()

    reconstructed, first_receipt = reveal_heldout_archive(
        manifest,
        ciphertext,
        key=KEY,
        candidate_freeze_id=CANDIDATE_ID,
        candidate_freeze_sha256=CANDIDATE_SHA256,
    )
    repeated = verify_revealed_archive(
        HeldoutManifest.from_bytes(manifest.canonical_bytes()),
        reconstructed.canonical_bytes(),
        HeldoutRevealReceipt.from_bytes(receipt.canonical_bytes()),
        expected_candidate_freeze_id=CANDIDATE_ID,
        expected_candidate_freeze_sha256=CANDIDATE_SHA256,
    )

    assert (
        reconstructed
        == repeated
        == HeldoutArchive(
            schema_version="cernora.reference.heldout-archive/v1",
            cases=tuple(HeldoutArchiveCase.model_validate(case) for case in _cases()),
        )
    )
    assert len(manifest.case_commitments) == 3
    assert tuple(item.case_id for item in manifest.case_commitments) == tuple(
        case["case_id"] for case in _cases()
    )
    assert sha256_bytes(ciphertext) == manifest.ciphertext_sha256
    assert first_receipt == receipt
    assert receipt.manifest_id == manifest.manifest_id
    assert "key" not in receipt.canonical_bytes().decode()
    assert "/private/" not in receipt.canonical_bytes().decode()


@pytest.mark.parametrize(
    ("key", "nonce", "message"),
    (
        (b"short", NONCE, "32-byte key"),
        (KEY, b"short", "12-byte nonce"),
    ),
)
def test_seal_requires_exact_aes_gcm_secret_sizes(key: bytes, nonce: bytes, message: str) -> None:
    with pytest.raises(HeldoutSealError, match=message):
        seal_heldout_cases(_cases(), key=key, nonce=nonce)


@pytest.mark.parametrize(
    "cases",
    (
        _cases()[:2],
        (*_cases(), _cases()[0]),
        (_cases()[1], _cases()[0], _cases()[2]),
        (_cases()[0], _cases()[0], _cases()[2]),
    ),
)
def test_seal_requires_exact_ordered_unique_three_case_suite(
    cases: tuple[dict[str, object], ...],
) -> None:
    with pytest.raises(HeldoutSealError, match="canonical archive"):
        seal_heldout_cases(cases, key=KEY, nonce=NONCE)


def test_reveal_rejects_ciphertext_tampering() -> None:
    manifest, ciphertext, receipt = _sealed()
    altered = bytes([ciphertext[0] ^ 1]) + ciphertext[1:]

    with pytest.raises(HeldoutSealError, match="digest"):
        reveal_heldout_archive(
            manifest,
            altered,
            key=KEY,
            candidate_freeze_id=CANDIDATE_ID,
            candidate_freeze_sha256=CANDIDATE_SHA256,
        )


def test_reveal_rejects_wrong_but_well_formed_key() -> None:
    manifest, ciphertext, _ = _sealed()

    with pytest.raises(HeldoutSealError, match="authentication failed"):
        reveal_heldout_archive(
            manifest,
            ciphertext,
            key=OTHER_KEY,
            candidate_freeze_id=CANDIDATE_ID,
            candidate_freeze_sha256=CANDIDATE_SHA256,
        )


def test_reveal_rejects_candidate_or_manifest_mismatch() -> None:
    manifest, ciphertext, receipt = _sealed()
    other_manifest, _ = seal_heldout_cases(_cases(), key=KEY, nonce=b"z" * 12)
    archive, _ = reveal_heldout_archive(
        manifest,
        ciphertext,
        key=KEY,
        candidate_freeze_id=CANDIDATE_ID,
        candidate_freeze_sha256=CANDIDATE_SHA256,
    )

    with pytest.raises(HeldoutSealError, match="frozen Candidate"):
        verify_revealed_archive(
            manifest,
            archive.canonical_bytes(),
            receipt,
            expected_candidate_freeze_id="candidate-freeze-002",
            expected_candidate_freeze_sha256=CANDIDATE_SHA256,
        )
    with pytest.raises(HeldoutSealError, match="supplied manifest"):
        verify_revealed_archive(
            other_manifest,
            archive.canonical_bytes(),
            receipt,
            expected_candidate_freeze_id=CANDIDATE_ID,
            expected_candidate_freeze_sha256=CANDIDATE_SHA256,
        )


def test_reveal_rejects_reidentified_aad_tampering() -> None:
    manifest, ciphertext, receipt = _sealed()
    payload = manifest.model_dump(mode="json")
    payload["archive_sha256"] = "f" * 64
    forged = _reidentify_manifest(payload)

    with pytest.raises(HeldoutSealError, match="authentication failed"):
        reveal_heldout_archive(
            forged,
            ciphertext,
            key=KEY,
            candidate_freeze_id=CANDIDATE_ID,
            candidate_freeze_sha256=CANDIDATE_SHA256,
        )


def test_manifest_and_receipt_reject_noncanonical_identity() -> None:
    manifest, _, receipt = _sealed()
    manifest_payload = manifest.model_dump(mode="json")
    manifest_payload["manifest_id"] = "0" * 64
    receipt_payload = receipt.model_dump(mode="json")
    receipt_payload["receipt_id"] = "0" * 64

    with pytest.raises(ValidationError, match="manifest identity"):
        HeldoutManifest.model_validate(manifest_payload)
    with pytest.raises(ValidationError, match="receipt identity"):
        HeldoutRevealReceipt.model_validate(receipt_payload)


@pytest.mark.parametrize("field", ("task_authority_id", "task_authority_sha256"))
def test_public_reveal_rejects_reidentified_task_binding_tampering(field: str) -> None:
    manifest, ciphertext, receipt = _sealed()
    archive, _ = reveal_heldout_archive(
        manifest,
        ciphertext,
        key=KEY,
        candidate_freeze_id=CANDIDATE_ID,
        candidate_freeze_sha256=CANDIDATE_SHA256,
    )
    payload = receipt.model_dump(mode="json", exclude={"receipt_id"})
    records = payload["case_records"]
    assert isinstance(records, list)
    first = records[0]
    assert isinstance(first, dict)
    first[field] = "0" * 64
    payload["receipt_id"] = canonical_content_id(payload, excluded=frozenset())
    forged = HeldoutRevealReceipt.model_validate(payload)

    with pytest.raises(HeldoutSealError, match="revealed authorities"):
        verify_revealed_archive(
            manifest,
            archive.canonical_bytes(),
            forged,
            expected_candidate_freeze_id=CANDIDATE_ID,
            expected_candidate_freeze_sha256=CANDIDATE_SHA256,
        )


def test_checked_in_prefreeze_artifacts_are_public_commitments_only() -> None:
    root = Path(__file__).resolve().parents[2] / "examples/m4-heldout-sealed"
    if not root.exists():
        pytest.skip("custodian has not materialized the sealed suite yet")
    assert {path.name for path in root.iterdir()} == {"ciphertext.bin", "manifest.json"}
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = HeldoutManifest.from_bytes(manifest_bytes)
    ciphertext = (root / "ciphertext.bin").read_bytes()
    assert manifest.canonical_bytes() == manifest_bytes
    assert len(ciphertext) == manifest.ciphertext_size
    assert sha256_bytes(ciphertext) == manifest.ciphertext_sha256


def test_seal_script_rejects_plaintext_and_key_in_every_git_worktree(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts/create_m4_heldout_seal.py"
    private_archive = _canonical_dummy_archive(tmp_path / "archive.json")
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}

    for index, worktree in enumerate(_representative_worktrees(tmp_path)):
        for target_kind in ("archive", "key"):
            blocked = worktree / f".custody-negative-{index}-{target_kind}"
            assert not blocked.exists()
            output = tmp_path / f"sealed-{index}-{target_kind}"
            key_output = blocked if target_kind == "key" else tmp_path / f"key-{index}"
            archive = blocked if target_kind == "archive" else private_archive
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--archive",
                    str(archive),
                    "--output-dir",
                    str(output),
                    "--key-output",
                    str(key_output),
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0
            assert "outside every Git worktree" in result.stderr
            assert not blocked.exists()
            assert not output.exists()


def test_seal_script_rolls_back_only_its_new_key_after_late_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script, module = _load_seal_script()
    archive = _canonical_dummy_archive(tmp_path / "archive.json")
    output = tmp_path / "sealed"
    key_output = tmp_path / "key.bin"

    def fail_after_key_created(path: Path) -> None:
        raise OSError(f"synthetic directory sync failure: {path.name}")

    monkeypatch.setattr(module, "_sync_directory", fail_after_key_created)
    with pytest.raises(OSError, match="synthetic directory sync failure"):
        script.main(
            [
                "--archive",
                str(archive),
                "--output-dir",
                str(output),
                "--key-output",
                str(key_output),
            ]
        )
    assert not key_output.exists()
    assert not output.exists()


def test_seal_script_never_removes_a_preexisting_key_path(tmp_path: Path) -> None:
    script, _ = _load_seal_script()
    archive = _canonical_dummy_archive(tmp_path / "archive.json")
    output = tmp_path / "sealed"
    key_output = tmp_path / "existing-key.bin"
    sentinel = b"preexisting-nonsecret-test-data"
    key_output.write_bytes(sentinel)

    with pytest.raises(FileExistsError):
        script.main(
            [
                "--archive",
                str(archive),
                "--output-dir",
                str(output),
                "--key-output",
                str(key_output),
            ]
        )
    assert key_output.read_bytes() == sentinel
    assert not output.exists()
