"""Portable observations derived from actual pi Runtime artifacts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from cernora_reference_workflow.common import ContractError, sha256_file, validate_relative_path
from cernora_reference_workflow.experiment_spec import Digest, NonEmpty, StrictContract

_PROHIBITED_TELEMETRY_MARKERS = (
    b'"type":"analytics"',
    b'"type": "analytics"',
    b'"type":"telemetry"',
    b'"type": "telemetry"',
    b"api.segment.io",
    b"api.honeycomb.io",
    b"pi.dev/api/",
    b"opentelemetry",
    b"sentry.io",
)
_PI_MESSAGE_END = re.compile(rb'"type"\s*:\s*"message_end"')
_PI_ASSISTANT_ROLE = re.compile(rb'"role"\s*:\s*"assistant"')
_PI_USAGE = re.compile(rb'"usage"\s*:\s*\{')

CONTAINER_CLEANUP_RECEIPT = {"trial_container_absent": True}


class ContainerImageObservation(StrictContract):
    schema_version: Literal["cernora.reference.container-image-observation/v1"]
    task_image_reference: NonEmpty
    observed_image_id: Annotated[
        StrictStr,
        Field(pattern=r"^sha256:[0-9a-f]{64}$"),
    ]
    matched: Literal[True]

    @model_validator(mode="after")
    def validate_match(self) -> ContainerImageObservation:
        expected = self.task_image_reference.rsplit("@", 1)[-1]
        if expected != self.observed_image_id:
            raise ValueError("observed container image ID does not match task image reference")
        return self


class RuntimeArtifactObservation(StrictContract):
    path: NonEmpty
    byte_length: Annotated[StrictInt, Field(ge=0)]
    sha256: Digest

    @model_validator(mode="after")
    def validate_path(self) -> RuntimeArtifactObservation:
        validate_relative_path(self.path)
        if not (
            self.path in {"runtime/trajectory.json", "runtime/pi-events.jsonl"}
            or self.path.startswith("runtime/pi-session/")
        ):
            raise ValueError("Runtime observation path is not an approved pi artifact")
        return self


class RuntimeBoundaryObservation(StrictContract):
    schema_version: Literal["cernora.reference.runtime-boundary-observation/v1"]
    effective_config_sha256: Digest
    effective_features_sha256: Digest
    runtime_policy_sha256: Digest
    runtime_cleanup_sha256: Digest
    container_cleanup_sha256: Digest
    inspected_artifacts: tuple[RuntimeArtifactObservation, ...]
    prohibited_telemetry_matches: Literal[0]
    provider_egress: Literal["observed", "not-observed"]
    provider_evidence_sha256: Digest | None

    @model_validator(mode="after")
    def validate_observation(self) -> RuntimeBoundaryObservation:
        paths = tuple(item.path for item in self.inspected_artifacts)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("inspected Runtime artifacts must be uniquely sorted")
        evidence = {item.sha256 for item in self.inspected_artifacts}
        if self.provider_egress == "observed":
            if self.provider_evidence_sha256 not in evidence:
                raise ValueError("provider egress must bind an inspected pi artifact")
        elif self.provider_evidence_sha256 is not None:
            raise ValueError("unobserved provider egress cannot bind invented evidence")
        return self


def inspect_runtime_artifacts(
    artifacts: tuple[tuple[str, Path], ...],
    *,
    effective_config_sha256: str,
    effective_features_sha256: str,
    runtime_policy_sha256: str,
    runtime_cleanup_sha256: str,
    container_cleanup_sha256: str,
) -> RuntimeBoundaryObservation:
    """Regenerate the boundary observation strictly from frozen pi Runtime artifacts.

    ``effective_config_sha256`` and ``effective_features_sha256`` bind the pi
    environment receipt and the pi version receipt of the same export.
    """

    observations: list[RuntimeArtifactObservation] = []
    provider_evidence_sha256: str | None = None
    for relative, path in sorted(artifacts):
        data = path.read_bytes()
        if any(marker in data.lower() for marker in _PROHIBITED_TELEMETRY_MARKERS):
            raise ContractError(
                f"Runtime artifact contains prohibited telemetry evidence: {relative}"
            )
        digest = sha256_file(path)
        observations.append(
            RuntimeArtifactObservation(
                path=relative,
                byte_length=len(data),
                sha256=digest,
            )
        )
        # Provider evidence is event-type-level, not substring-level: an authoritative
        # streamed ``message_end`` record from a real provider call. Prompt text alone
        # containing ``"role":"assistant"`` and ``"usage":{`` cannot forge it.
        if (
            provider_evidence_sha256 is None
            and _PI_MESSAGE_END.search(data)
            and _PI_ASSISTANT_ROLE.search(data)
            and _PI_USAGE.search(data)
        ):
            provider_evidence_sha256 = digest
    return RuntimeBoundaryObservation(
        schema_version="cernora.reference.runtime-boundary-observation/v1",
        effective_config_sha256=effective_config_sha256,
        effective_features_sha256=effective_features_sha256,
        runtime_policy_sha256=runtime_policy_sha256,
        runtime_cleanup_sha256=runtime_cleanup_sha256,
        container_cleanup_sha256=container_cleanup_sha256,
        inspected_artifacts=tuple(observations),
        prohibited_telemetry_matches=0,
        provider_egress="observed" if provider_evidence_sha256 is not None else "not-observed",
        provider_evidence_sha256=provider_evidence_sha256,
    )


__all__ = [
    "CONTAINER_CLEANUP_RECEIPT",
    "ContainerImageObservation",
    "RuntimeArtifactObservation",
    "RuntimeBoundaryObservation",
    "inspect_runtime_artifacts",
]
