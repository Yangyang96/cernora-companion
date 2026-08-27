from __future__ import annotations

import hashlib
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from cernora import component_identity
from pydantic import JsonValue

from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.controlled_execution import ControlledAttemptRequest
from cernora_reference_workflow.controlled_experiment_spec import (
    ControlledExperimentSpecV2,
    DatasetCaseAuthority,
    EvaluationCaseIdentitySource,
    materialize_authority_source,
    materialize_controlled_experiment_spec,
    materialize_dataset_authority,
    materialize_expected_evaluation_authority,
    materialize_expected_evaluation_policy,
)
from cernora_reference_workflow.controlled_live_attempt import (
    ControlledHarborAttemptExecutor,
    LiveAttemptError,
    validate_installed_harbor_cli,
)
from cernora_reference_workflow.controlled_profile import (
    GATE_VERSION,
    PROFILE_ID,
    PROFILE_VERSION,
    PROJECTION_VERSION,
    SCORER_VERSION,
    build_controlled_profile_authority,
)
from cernora_reference_workflow.controlled_run_plan import ControlledTrialSlotV2
from cernora_reference_workflow.controlled_runtime import SubprocessResult
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
)
from cernora_reference_workflow.runtime_policy import (
    CODEX_RUNTIME_INSTALLATION,
    RUNTIME_CLEANUP_RECEIPT,
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
    TELEMETRY_CONFIG_TOML,
)
from tests.unit.test_controlled_experiment_spec import valid_payload


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _proxy_environment() -> dict[str, str]:
    return {
        "http_proxy": "http://proxy.invalid:8080",
        "https_proxy": "http://proxy.invalid:8080",
        "all_proxy": "socks5://proxy.invalid:1080",
    }


def _spec(task: ControlledTaskAuthority) -> ControlledExperimentSpecV2:
    payload = valid_payload(
        case_id=task.case.case_id,
        configuration_id="baseline",
        prompt="Repair the project using the frozen task evidence.",
    )
    runtime_source = materialize_authority_source(
        "runtime",
        cast(
            JsonValue,
            {
                "codex_config_toml_sha256": sha256_bytes(TELEMETRY_CONFIG_TOML.encode("utf-8")),
                "codex_runtime_installation": CODEX_RUNTIME_INSTALLATION,
                "policy": RUNTIME_POLICY,
                "strict_config": True,
            },
        ),
    )
    assert runtime_source.source_sha256 == RUNTIME_CONFIGURATION_SHA256
    runtime = payload["runtime"]
    assert isinstance(runtime, dict)
    runtime["configuration_sha256"] = runtime_source.source_sha256
    runtime["configuration_source"] = runtime_source.model_dump(mode="json")
    task_source = materialize_authority_source(
        "task", cast(JsonValue, task.case.model_dump(mode="json"))
    )
    task_prompt = materialize_authority_source(
        "task-prompt", {"case": task.case.case_id, "text": task.case.input.prompt}
    )
    task_instruction = materialize_authority_source(
        "task-instruction", {"case": task.case.case_id, "text": "Satisfy the frozen tests."}
    )
    test_authority = materialize_authority_source(
        "test-authority", {"task_authority_id": task.authority_id}
    )
    test_plan = materialize_authority_source("test-plan", {"command": list(task.test_command)})
    test_files = {
        "files": [
            {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
            for item in task.test_files
        ]
    }
    test_source = materialize_authority_source("test-source", cast(JsonValue, test_files))
    assert test_source.source_sha256 == task.test_source_sha256
    profile = build_controlled_profile_authority((task,))
    profile_source = materialize_authority_source(
        "profile", cast(JsonValue, profile.model_dump(mode="json", exclude_none=False))
    )
    case_identity = {
        "case_id": task.case.case_id,
        "case_version": task.case.case_version,
        "case_set": task.case.case_set,
        "sha256": task_source.source_sha256,
    }
    profile_identity = {
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "sha256": profile_source.source_sha256,
    }
    projection = {
        "name": "imported_projection",
        "version": PROJECTION_VERSION,
        "sha256": sha256_bytes(
            canonical_json_bytes({"name": "imported_projection", "version": PROJECTION_VERSION})
        ),
        "digest_kind": "identity",
    }
    scorer = component_identity("scorer", SCORER_VERSION)
    gate = component_identity("gate_policy", GATE_VERSION)
    expected = materialize_expected_evaluation_authority(
        {
            "schema_version": "agent.evaluator.imported-evaluation-authority/v1",
            "profile": profile_identity,
            "case": case_identity,
            "fixtures": [item.model_dump(mode="json") for item in task.case.fixture_references],
            "projection": projection,
            "scorer": scorer.model_dump(mode="json"),
            "case_gate": gate.model_dump(mode="json"),
        }
    )
    expected_policy = materialize_expected_evaluation_policy(
        {
            "schema_version": "agent.evaluator.comparison-evaluation-policy/v1",
            "profile": profile_identity,
            "projection": projection,
            "scorer": scorer.model_dump(mode="json"),
            "case_gate": gate.model_dump(mode="json"),
        }
    )
    container = payload["container"]
    assert isinstance(container, dict)
    dataset_case = DatasetCaseAuthority(
        case=EvaluationCaseIdentitySource.model_validate(case_identity),
        task_source_sha256=task_source.source_sha256,
        task_prompt_sha256=task_prompt.source_sha256,
        task_instruction_sha256=task_instruction.source_sha256,
        allowed_paths=task.allowed_paths,
        protected_paths=task.protected_paths,
        task_image=cast(str, container["image"]),
        build_base_image=cast(str, container["build_base_image"]),
        test_authority_id="synthetic-python-test-runner",
        test_authority_version="1",
        test_authority_sha256=test_authority.source_sha256,
        test_plan_sha256=test_plan.source_sha256,
        test_source_sha256=test_source.source_sha256,
        test_command=task.test_command,
        test_working_directory="candidate",
        fixtures=task.case.fixture_references,
    )
    payload["task"] = {
        "task_id": task.case.case_id,
        "task_version": task.case.case_version,
        "case_set": task.case.case_set,
        "content_sha256": task_source.source_sha256,
        "task_source": task_source.model_dump(mode="json"),
        "prompt_sha256": task_prompt.source_sha256,
        "prompt_source": task_prompt.model_dump(mode="json"),
        "instruction_sha256": task_instruction.source_sha256,
        "instruction_source": task_instruction.model_dump(mode="json"),
        "allowed_paths": list(task.allowed_paths),
        "protected_paths": list(task.protected_paths),
    }
    payload["test_runner"] = {
        "authority_id": "synthetic-python-test-runner",
        "authority_version": "1",
        "authority_sha256": test_authority.source_sha256,
        "authority_source": test_authority.model_dump(mode="json"),
        "test_plan_sha256": test_plan.source_sha256,
        "test_plan_source": test_plan.model_dump(mode="json"),
        "test_source_sha256": test_source.source_sha256,
        "test_source": test_source.model_dump(mode="json"),
        "command": list(task.test_command),
        "working_directory": "candidate",
    }
    payload["profile"] = {
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "authority_sha256": profile_source.source_sha256,
        "authority_source": profile_source.model_dump(mode="json"),
    }
    payload["expected_evaluation_authority"] = expected.model_dump(mode="json")
    payload["expected_evaluation_policy"] = expected_policy.model_dump(mode="json")
    payload["dataset_authority"] = materialize_dataset_authority((dataset_case,)).model_dump(
        mode="json"
    )
    return materialize_controlled_experiment_spec(payload)


def _request(spec: ControlledExperimentSpecV2, *, trial: str = "live") -> ControlledAttemptRequest:
    slot = ControlledTrialSlotV2(
        schema_version="cernora.reference.controlled-trial-slot/v2",
        trial_slot_id=digest(f"slot:{trial}"),
        run_plan_id=digest("run-plan"),
        slot_index=1,
        case_id=spec.task.task_id,
        configuration_id=spec.configuration_id,
        experiment_id=spec.experiment_id,
        repetition=1,
    )
    return ControlledAttemptRequest(
        trial_id=digest(trial),
        slot=slot,
        specification=spec,
        ordinal=1,
        predecessor_attempt_id=None,
        global_deadline_monotonic=100.0,
    )


class FakeProcess:
    def __init__(self, task: ControlledTaskAuthority, spec: ControlledExperimentSpecV2) -> None:
        self.task = task
        self.spec = spec
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline_monotonic: float,
        timeout_seconds: int,
    ) -> SubprocessResult:
        del cwd, deadline_monotonic, timeout_seconds
        self.commands.append(command)
        del environment
        task_root = Path(command[command.index("-p") + 1])
        job_root = Path(command[command.index("-o") + 1])
        job_name = command[command.index("--job-name") + 1]
        verifier = job_root / job_name / "trial-1" / "verifier"
        verifier.mkdir(parents=True)
        (verifier.parent / "result.json").write_bytes(b"{}")
        agent = verifier.parent / "agent"
        agent.mkdir()
        (agent / "effective-config.toml").write_text(TELEMETRY_CONFIG_TOML, encoding="utf-8")
        (agent / "runtime-policy.json").write_bytes(canonical_json_bytes(RUNTIME_POLICY))
        (agent / "runtime-cleanup.json").write_bytes(canonical_json_bytes(RUNTIME_CLEANUP_RECEIPT))
        (agent / "effective-features.txt").write_text(
            "plugins stable false\nunified_exec stable true\n", encoding="utf-8"
        )
        shutil.copytree(task_root / "environment/workspace", verifier / "candidate")
        (verifier / "exit-code.txt").write_text("1\n", encoding="ascii")
        return SubprocessResult(
            status="exited",
            exit_code=0,
            stdout=b"provider output is not portable evidence",
            stderr=b"http://127.0.0.1:9981/private/user/path",
            started_monotonic=0.0,
            finished_monotonic=1.0,
            receipt_sha256=digest("process"),
        )


def test_live_executor_observes_authorities_and_emits_real_core_package(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    process = FakeProcess(task, spec)
    cleanups: list[str] = []
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=tmp_path,
        tasks=(task,),
        evaluation_root=evaluation_root,
        environment_provider=lambda: {
            **_proxy_environment(),
            "CERNORA_AUTH_FILE": "/private/auth.json",
        },
        process_runner=process,
        cleanup_verifier=cleanups.append,
        cli_validator=lambda _: None,
        image_verifier=lambda value: value.container.image.rsplit("@sha256:", 1)[1],
    )

    attempt = executor(_request(spec))

    assert attempt.evaluation is not None
    assert attempt.repair_result is not None and not attempt.repair_result.passed
    assert attempt.retry_eligible is False
    assert attempt.runtime_observation is not None
    attempt.runtime_observation.verify(spec)
    portable = canonical_json_bytes(attempt.model_dump(mode="json"))
    assert b"9981" not in portable
    assert b"/private/" not in portable
    assert cleanups and process.commands
    command = process.commands[0]
    assert command[1:3] == ("run", "-p")
    assert all(
        unsupported not in command
        for unsupported in (
            "--task-path",
            "--output-dir",
            "--reasoning-effort",
            "--task-image",
            "--native-retries",
            "--concurrency",
        )
    )


def test_live_executor_fails_closed_on_applied_prompt_or_image_drift(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    process = FakeProcess(task, spec)
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()

    class DriftedExecutor(ControlledHarborAttemptExecutor):
        def _command(
            self,
            request: ControlledAttemptRequest,
            task_root: Path,
            job_root: Path,
            job_name: str,
            proxy_environment: Mapping[str, str],
        ) -> tuple[str, ...]:
            command = list(
                super()._command(
                    request,
                    task_root,
                    job_root,
                    job_name,
                    proxy_environment,
                )
            )
            command[command.index("--override-memory-mb") + 1] = "2048"
            return tuple(command)

    executor = DriftedExecutor(
        repository_root=tmp_path,
        tasks=(task,),
        evaluation_root=evaluation_root,
        environment_provider=_proxy_environment,
        process_runner=process,
        cleanup_verifier=lambda _: None,
        cli_validator=lambda _: None,
        image_verifier=lambda value: value.container.image.rsplit("@sha256:", 1)[1],
    )

    with pytest.raises(LiveAttemptError, match="drifts"):
        executor(_request(spec, trial="drift"))


def test_live_executor_marks_only_eligible_preterminal_failure_for_retry(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()

    def start_failure(
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline_monotonic: float,
        timeout_seconds: int,
    ) -> SubprocessResult:
        del command, cwd, environment, deadline_monotonic, timeout_seconds
        return SubprocessResult(
            status="start_failure",
            exit_code=None,
            stdout=b"",
            stderr=b"",
            started_monotonic=0.0,
            finished_monotonic=0.0,
            receipt_sha256=digest("start-failure"),
        )

    executor = ControlledHarborAttemptExecutor(
        repository_root=tmp_path,
        tasks=(task,),
        evaluation_root=evaluation_root,
        environment_provider=_proxy_environment,
        process_runner=start_failure,
        cleanup_verifier=lambda _: None,
        cli_validator=lambda _: None,
        image_verifier=lambda value: value.container.image.rsplit("@sha256:", 1)[1],
    )

    attempt = executor(_request(spec, trial="eligible-retry"))

    assert attempt.retry_eligible is True
    assert attempt.lifecycle is not None
    assert attempt.lifecycle.category == "infrastructure_start_failure"


def test_installed_harbor_help_matches_production_command_surface() -> None:
    validate_installed_harbor_cli(Path(sys.executable).with_name("harbor"))
