"""Closed Candidate Development authority, isolated from held-out evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, Self

from pydantic import Field, StrictStr, field_validator, model_validator

from cernora_reference_workflow.common import ContractError, canonical_content_id
from cernora_reference_workflow.experiment_spec import Digest, StrictContract

Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
NonEmpty = Annotated[StrictStr, Field(min_length=1)]
TreatmentAxis = Literal[
    "prompt-instruction",
    "model",
    "tool-schema",
    "generation-configuration",
    "runtime-version",
]


class BaselineAuthority(StrictContract):
    configuration_id: Literal["baseline"]
    authority_sha256: Digest


class CandidatePatch(StrictContract):
    """One declared Treatment applied over the frozen Baseline authority."""

    configuration_id: Literal["candidate"]
    baseline_authority_sha256: Digest
    authority_sha256: Digest
    treatment_axis: TreatmentAxis
    treatment_sha256: Digest

    @model_validator(mode="after")
    def substantive_change(self) -> Self:
        if self.authority_sha256 == self.baseline_authority_sha256:
            raise ValueError("CandidatePatch must change the Baseline authority")
        return self


class CandidateHypothesis(StrictContract):
    """Predeclared causal account for one Candidate intervention."""

    observed_failure_code: Identifier
    mechanism: NonEmpty
    intervention_scope: NonEmpty
    expected_observation: NonEmpty
    falsifier: NonEmpty


class DevelopmentObservation(StrictContract):
    """Development or regression evidence that can never become held-out evidence."""

    observation_id: Identifier
    case_id: Identifier
    split: Literal["development", "regression"]
    source: Literal["agent-pilot", "verifier-calibration"]
    agent_outcome: Literal["behavioral-failure", "pass", "not-observed"]
    failure_code: Identifier | None
    evidence_sha256: Digest

    @model_validator(mode="after")
    def coherent_source(self) -> Self:
        if self.source == "verifier-calibration":
            if self.agent_outcome != "not-observed" or self.failure_code is not None:
                raise ValueError("verifier calibration cannot claim an Agent observation")
        elif self.agent_outcome == "not-observed":
            raise ValueError("Agent pilot must record an Agent outcome")
        if (self.agent_outcome == "behavioral-failure") != (self.failure_code is not None):
            raise ValueError("Agent behavioral failure and failure code must appear together")
        return self


class CandidateDevelopmentRecord(StrictContract):
    """Content-addressed Candidate authority frozen before held-out reveal."""

    schema_version: Literal["cernora.reference.candidate-development/v1"]
    development_id: Digest
    baseline: BaselineAuthority
    candidate: CandidatePatch
    hypothesis: CandidateHypothesis
    observations: tuple[DevelopmentObservation, ...] = Field(min_length=1)

    @field_validator("observations", mode="before")
    @classmethod
    def tuple_observations(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def closed_development_authority(self) -> Self:
        observation_ids = tuple(item.observation_id for item in self.observations)
        if observation_ids != tuple(sorted(observation_ids)) or len(observation_ids) != len(
            set(observation_ids)
        ):
            raise ValueError("Candidate Development observations must be sorted and unique")
        if self.candidate.baseline_authority_sha256 != self.baseline.authority_sha256:
            raise ValueError("CandidatePatch does not derive from the frozen Baseline")
        matching_agent_failure = any(
            item.source == "agent-pilot"
            and item.agent_outcome == "behavioral-failure"
            and item.failure_code == self.hypothesis.observed_failure_code
            for item in self.observations
        )
        if not matching_agent_failure:
            raise ValueError("Candidate Development requires the hypothesized Agent failure")
        expected = canonical_content_id(
            self.model_dump(mode="json"), excluded=frozenset({"development_id"})
        )
        if self.development_id != expected:
            raise ValueError("Candidate Development identity does not match canonical content")
        return self


def freeze_candidate_development(
    payload_without_identity: Mapping[str, object],
) -> CandidateDevelopmentRecord:
    """Freeze one Candidate from development-only authority."""

    if "development_id" in payload_without_identity:
        raise ContractError("Candidate Development input must omit development_id")
    payload = dict(payload_without_identity)
    payload["development_id"] = canonical_content_id(payload, excluded=frozenset())
    return CandidateDevelopmentRecord.model_validate(payload)


def candidate_continuity_violations(
    prior: CandidateDevelopmentRecord,
    remint: CandidateDevelopmentRecord,
) -> tuple[str, ...]:
    """Fail-closed comparison between one frozen record and its case-set re-mint.

    Study materialization must re-mint the canonical Candidate Development record
    over the final study case set (its configuration authorities are case-set
    derived), using byte-identical treatment payload bytes. This gate reports every
    way a re-mint drifted from the frozen development content: only the
    case-set-dependent Baseline/Candidate configuration authority digests — and
    therefore the ``development_id`` they feed — are allowed to change.
    """

    violations: list[str] = []
    if remint.hypothesis != prior.hypothesis:
        violations.append("hypothesis drifted from the frozen Candidate")
    if remint.observations != prior.observations:
        violations.append("observations drifted from the frozen Candidate")
    if remint.candidate.treatment_axis != prior.candidate.treatment_axis:
        violations.append("treatment axis drifted from the frozen Candidate")
    if remint.candidate.treatment_sha256 != prior.candidate.treatment_sha256:
        violations.append("treatment digest drifted from the frozen Candidate")
    return tuple(violations)


__all__ = [
    "BaselineAuthority",
    "CandidateDevelopmentRecord",
    "CandidateHypothesis",
    "CandidatePatch",
    "DevelopmentObservation",
    "candidate_continuity_violations",
    "freeze_candidate_development",
]
