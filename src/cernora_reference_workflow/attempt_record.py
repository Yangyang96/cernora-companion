"""Atomic closed publication for attempts that never produce a completed export."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Literal

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    closed_regular_tree,
    load_json_file,
    sha256_bytes,
    sha256_file,
)
from cernora_reference_workflow.experiment_spec import Digest, NonEmpty, StrictContract
from cernora_reference_workflow.lifecycle import TerminalRecord
from cernora_reference_workflow.publication import atomic_publish_directory
from cernora_reference_workflow.secrets import require_secret_free


class PreterminalAttemptManifest(StrictContract):
    schema_version: Literal["cernora.reference.preterminal-attempt/v1"]
    record_id: Digest
    experiment_id: Digest
    attempt_id: Digest
    source_trial_id: NonEmpty
    terminal_sha256: Digest

    @classmethod
    def materialize(
        cls,
        *,
        experiment_id: str,
        terminal: TerminalRecord,
        source_trial_id: str,
        terminal_sha256: str,
    ) -> PreterminalAttemptManifest:
        payload = {
            "schema_version": "cernora.reference.preterminal-attempt/v1",
            "experiment_id": experiment_id,
            "attempt_id": terminal.attempt_id,
            "source_trial_id": source_trial_id,
            "terminal_sha256": terminal_sha256,
        }
        payload["record_id"] = sha256_bytes(canonical_json_bytes(payload))
        return cls.model_validate(payload)


def verify_preterminal_attempt(root: Path) -> PreterminalAttemptManifest:
    files = closed_regular_tree(root)
    if set(files) != {"manifest.json", "terminal.json"}:
        raise ContractError("pre-terminal attempt record has an invalid closed file set")
    manifest_payload = load_json_file(files["manifest.json"])
    terminal_payload = load_json_file(files["terminal.json"])
    if not isinstance(manifest_payload, dict) or not isinstance(terminal_payload, dict):
        raise ContractError("pre-terminal attempt files must contain JSON objects")
    manifest = PreterminalAttemptManifest.model_validate(manifest_payload)
    terminal = TerminalRecord.model_validate(terminal_payload)
    if files["manifest.json"].read_bytes() != canonical_json_bytes(
        manifest.model_dump(mode="json")
    ):
        raise ContractError("pre-terminal attempt manifest is not canonical JSON")
    if files["terminal.json"].read_bytes() != canonical_json_bytes(
        terminal.model_dump(mode="json")
    ):
        raise ContractError("pre-terminal terminal receipt is not canonical JSON")
    if terminal.attempt_id != manifest.attempt_id:
        raise ContractError("pre-terminal manifest does not bind its terminal attempt")
    if sha256_file(files["terminal.json"]) != manifest.terminal_sha256:
        raise ContractError("pre-terminal terminal receipt digest mismatch")
    expected = sha256_bytes(
        canonical_json_bytes(manifest.model_dump(mode="json", exclude={"record_id"}))
    )
    if manifest.record_id != expected:
        raise ContractError("pre-terminal attempt record identity mismatch")
    require_secret_free(root)
    return manifest


def publish_preterminal_attempt(
    *,
    destination: Path,
    experiment_id: str,
    terminal: TerminalRecord,
    source_trial_id: str,
) -> PreterminalAttemptManifest:
    if not destination.parent.is_dir():
        raise ContractError("pre-terminal attempt parent must already exist")
    if destination.exists() or destination.is_symlink():
        raise ContractError("pre-terminal attempt destination must not already exist")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        terminal_path = staging / "terminal.json"
        terminal_path.write_bytes(canonical_json_bytes(terminal.model_dump(mode="json")))
        manifest = PreterminalAttemptManifest.materialize(
            experiment_id=experiment_id,
            terminal=terminal,
            source_trial_id=source_trial_id,
            terminal_sha256=sha256_file(terminal_path),
        )
        (staging / "manifest.json").write_bytes(
            canonical_json_bytes(manifest.model_dump(mode="json"))
        )
        verify_preterminal_attempt(staging)
        atomic_publish_directory(staging, destination)
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


__all__ = [
    "PreterminalAttemptManifest",
    "publish_preterminal_attempt",
    "verify_preterminal_attempt",
]
