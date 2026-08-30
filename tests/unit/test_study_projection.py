from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from cernora_reference_workflow.candidate_development import freeze_candidate_development
from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.controlled_study import (
    compile_study_protocol,
    materialize_heldout_commitment,
    materialize_implementation_lock,
    materialize_study_analysis_policy,
    materialize_study_intent,
)
from cernora_reference_workflow.execution import initialize_execution, reload_execution
from cernora_reference_workflow.study_projection import (
    bind_study_run_plan,
    case_authority_sha256,
    configuration_authority_sha256,
)
from tests.unit.test_controlled_run_plan import valid_m4_payload
from tests.unit.test_controlled_study import implementation_payload


def study_payload_for_m4() -> tuple[dict[str, object], ControlledRunPlanV2]:
    run_plan = materialize_controlled_run_plan(valid_m4_payload())
    cases = []
    for index, case in enumerate(run_plan.cases):
        split = "development" if index < 3 else "regression" if index < 6 else "held-out"
        cases.append(
            {
                "case_id": case.case_id,
                "split": split,
                "authority_sha256": case_authority_sha256(run_plan, case.case_id),
            }
        )
    development = freeze_candidate_development(
        {
            "schema_version": "cernora.reference.candidate-development/v1",
            "baseline": {
                "configuration_id": "baseline",
                "authority_sha256": configuration_authority_sha256(run_plan, "baseline"),
            },
            "candidate": {
                "configuration_id": "candidate",
                "baseline_authority_sha256": configuration_authority_sha256(run_plan, "baseline"),
                "authority_sha256": configuration_authority_sha256(run_plan, "candidate"),
                "treatment_axis": "prompt-instruction",
                "treatment_sha256": "c" * 64,
            },
            "hypothesis": {
                "observed_failure_code": "interval-boundary-v1",
                "mechanism": "The agent misses inclusive endpoint overlap.",
                "intervention_scope": "Prompt guidance for interval repair reasoning.",
                "expected_observation": "Fewer held-out boundary failures.",
                "falsifier": "No held-out improvement or a regression.",
            },
            "observations": [
                {
                    "observation_id": "agent-failure-001",
                    "case_id": cases[0]["case_id"],
                    "split": "development",
                    "source": "agent-pilot",
                    "agent_outcome": "behavioral-failure",
                    "failure_code": "interval-boundary-v1",
                    "evidence_sha256": "d" * 64,
                },
                {
                    "observation_id": "regression-calibration-001",
                    "case_id": cases[3]["case_id"],
                    "split": "regression",
                    "source": "verifier-calibration",
                    "agent_outcome": "not-observed",
                    "failure_code": None,
                    "evidence_sha256": "e" * 64,
                },
            ],
        }
    )
    policy = materialize_study_analysis_policy(
        {
            "schema_version": "cernora.reference.study-analysis-policy/v1",
            "primary_outcome": "paired-reliable-success-rate-delta",
            "bootstrap_resamples": 10000,
            "confidence_level": "0.95",
            "guardrail_rule": "no-protected-regression",
            "missing_evidence": "inconclusive",
            "claim_source": "held-out-only",
        }
    )
    policy_sha256 = sha256_bytes(canonical_json_bytes(policy.model_dump(mode="json")))
    heldout_cases = [item for item in cases if item["split"] == "held-out"]
    payload = {
        "schema_version": "cernora.reference.study-intent/v1",
        "study_kind": "confirmatory-effect",
        "candidate_development": development.model_dump(mode="json"),
        "cases": cases,
        "repetitions": run_plan.repetitions,
        "max_attempt_count": run_plan.execution.max_attempt_count,
        "max_wall_seconds": run_plan.execution.max_total_wall_time_seconds,
        "heldout_commitment": materialize_heldout_commitment(
            {
                "schema_version": "cernora.reference.heldout-commitment/v1",
                "manifest_sha256": "7" * 64,
                "case_count": len(heldout_cases),
                "case_commitment_root_sha256": sha256_bytes(canonical_json_bytes(heldout_cases)),
                "reveal_policy_sha256": "9" * 64,
            }
        ).model_dump(mode="json"),
        "analysis_policy": policy.model_dump(mode="json"),
        "implementation_lock": materialize_implementation_lock(
            implementation_payload(analysis_sha256=policy_sha256)
        ).model_dump(mode="json"),
    }
    return payload, run_plan


def test_study_protocol_binds_the_existing_m4_run_plan_without_core_changes() -> None:
    payload, run_plan = study_payload_for_m4()
    intent = materialize_study_intent(payload)
    protocol = compile_study_protocol(intent)
    binding = bind_study_run_plan(intent, protocol, run_plan)

    assert binding.run_plan_id == run_plan.run_plan_id
    assert binding.ordered_trial_slot_ids == tuple(
        item.trial_slot_id for item in run_plan.expand_trial_slots()
    )
    assert protocol.planned_trial_count == run_plan.planned_trial_count == 54


def test_existing_execution_engine_freezes_the_bound_v2_study_plan(tmp_path: Path) -> None:
    payload, run_plan = study_payload_for_m4()
    intent = materialize_study_intent(payload)
    protocol = compile_study_protocol(intent)
    binding = bind_study_run_plan(intent, protocol, run_plan)

    initialized = initialize_execution(tmp_path / "execution", run_plan, nonce="a" * 64)
    reloaded = reload_execution(tmp_path / "execution")

    assert initialized == reloaded
    assert reloaded.run_plan == run_plan
    assert tuple(item.slot.trial_slot_id for item in reloaded.trial_slots.slots) == (
        binding.ordered_trial_slot_ids
    )


def test_study_run_plan_binding_rejects_case_authority_drift() -> None:
    payload, run_plan = study_payload_for_m4()
    changed = deepcopy(payload)
    assert isinstance(changed["cases"], list)
    assert isinstance(changed["cases"][0], dict)
    changed["cases"][0]["authority_sha256"] = "0" * 64
    intent = materialize_study_intent(changed)

    with pytest.raises(ValueError, match="Case authority"):
        bind_study_run_plan(intent, compile_study_protocol(intent), run_plan)
