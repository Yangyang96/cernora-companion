"""Frozen builder for the Priority 4 Milestone 1 native exit RunPlan."""

from __future__ import annotations

from pathlib import Path

from cernora_reference_workflow.run_plan import RunPlan, materialize_run_plan
from cernora_reference_workflow.spec_builder import (
    TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    build_tiny_calculator_spec,
    build_tiny_calculator_v2_spec,
)


def build_m1_native_acceptance_plan(repository_root: Path) -> RunPlan:
    """Build the approved 2 Case x 2 Configuration x 3 repetition exit plan."""

    normal_v1 = build_tiny_calculator_spec(repository_root)
    timeout_v1 = build_tiny_calculator_spec(
        repository_root,
        timeout_seconds=3,
        agent_timeout_multiplier=TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    )
    normal_v2 = build_tiny_calculator_v2_spec(repository_root)
    timeout_v2 = build_tiny_calculator_v2_spec(
        repository_root,
        timeout_seconds=3,
        agent_timeout_multiplier=TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    )
    specifications = (normal_v1, timeout_v1, normal_v2, timeout_v2)
    cases = (normal_v1, normal_v2)
    cells = (
        (normal_v1, "normal-policy"),
        (timeout_v1, "short-timeout-policy"),
        (normal_v2, "normal-policy"),
        (timeout_v2, "short-timeout-policy"),
    )
    return materialize_run_plan(
        {
            "schema_version": "cernora.reference.run-plan/v1",
            "companion_version": "0.2.0",
            "cernora_version": "0.1.2",
            "connector": {
                "connector_id": "cernora-reference-harbor-pi",
                "connector_version": "2",
                "platform_qualification": "macos-arm64",
            },
            "experiment_specs": [item.model_dump(mode="json") for item in specifications],
            "cases": [
                {
                    "case_id": item.task.task_id,
                    "case_version": item.task.task_version,
                    "task_content_sha256": item.task.content_sha256,
                }
                for item in cases
            ],
            "configurations": [
                {"configuration_id": "normal-policy"},
                {"configuration_id": "short-timeout-policy"},
            ],
            "cells": [
                {
                    "case_id": specification.task.task_id,
                    "configuration_id": configuration_id,
                    "experiment_id": specification.experiment_id,
                }
                for specification, configuration_id in cells
            ],
            "repetitions": 3,
            "pairing_rule": "case-configuration-repetition",
            "planned_trial_count": 12,
            "worst_case_attempt_count": 24,
            "execution": {
                "concurrency": 1,
                "max_attempt_count": 24,
                "max_total_wall_time_seconds": 7200,
                "token_budget": {
                    "status": "unavailable",
                    "reason": "no-structured-authoritative-source",
                },
                "monetary_budget": {
                    "status": "unavailable",
                    "reason": "no-structured-authoritative-source",
                },
            },
            "analysis": {
                "method": "none",
                "method_version": "m1",
                "aggregate_quality_conclusion": False,
            },
        }
    )


__all__ = ["build_m1_native_acceptance_plan"]
