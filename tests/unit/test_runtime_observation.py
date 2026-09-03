from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.common import ContractError, sha256_file
from cernora_reference_workflow.runtime_observation import (
    ContainerImageObservation,
    inspect_runtime_artifacts,
)

DIGEST = "a" * 64


def test_container_image_observation_rejects_mismatched_image_id() -> None:
    with pytest.raises(ValueError, match="does not match task image reference"):
        ContainerImageObservation(
            schema_version="cernora.reference.container-image-observation/v1",
            task_image_reference=f"cernora-reference/test@sha256:{DIGEST}",
            observed_image_id=f"sha256:{'b' * 64}",
            matched=True,
        )


def _inspect(path: Path):  # type: ignore[no-untyped-def]
    return inspect_runtime_artifacts(
        (("runtime/pi-events.jsonl", path),),
        effective_config_sha256=DIGEST,
        effective_features_sha256=DIGEST,
        runtime_policy_sha256=DIGEST,
        runtime_cleanup_sha256=DIGEST,
        container_cleanup_sha256=DIGEST,
    )


def test_provider_egress_observation_binds_actual_agent_message_artifact(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_bytes(
        b'{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"done"}],'
        b'"usage":{"input":1,"output":2,"cacheRead":0,"cacheWrite":0,"totalTokens":3}}}\n'
    )
    observation = _inspect(events)
    assert observation.provider_egress == "observed"
    assert observation.provider_evidence_sha256 == sha256_file(events)
    assert observation.prohibited_telemetry_matches == 0


def test_missing_provider_event_is_explicit_not_observed(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_bytes(b'{"type":"session","version":3}\n')
    observation = _inspect(events)
    assert observation.provider_egress == "not-observed"
    assert observation.provider_evidence_sha256 is None


def test_prompt_text_cannot_forge_provider_egress_evidence(tmp_path: Path) -> None:
    """Substring-level role/usage pairs without a ``message_end`` event are not evidence."""

    events = tmp_path / "events.jsonl"
    events.write_bytes(
        b'{"type":"message","message":{"role":"user","content":'
        b'"the schema literal is \\"role\\":\\"assistant\\",\\"usage\\":{\\"input\\":1}"}}\n'
        b'{"echo":"assistant notes","role":"assistant","usage":{"input":1}}\n'
    )
    observation = _inspect(events)
    assert observation.provider_egress == "not-observed"
    assert observation.provider_evidence_sha256 is None


@pytest.mark.parametrize(
    "marker",
    (
        b'{"type":"telemetry"}\n',
        b'{"endpoint":"https://sentry.io/example"}\n',
        b'{"endpoint":"https://pi.dev/api/report-install"}\n',
        b'{"meter":"opentelemetry"}\n',
    ),
)
def test_exported_runtime_telemetry_markers_fail_closed(
    tmp_path: Path,
    marker: bytes,
) -> None:
    events = tmp_path / "events.jsonl"
    events.write_bytes(marker)
    with pytest.raises(ContractError, match="prohibited telemetry evidence"):
        _inspect(events)
