from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from cernora import component_identity
from pydantic import JsonValue

import cernora_reference_workflow.controlled_live_attempt as live_attempt_module
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
    ContainerRecord,
    ContainerSnapshot,
    ControlledHarborAttemptExecutor,
    LiveAttemptError,
    attributable_container_ids,
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
from cernora_reference_workflow.controlled_runner import ControlledActiveSafeStop
from cernora_reference_workflow.controlled_runtime import SubprocessResult
from cernora_reference_workflow.controlled_task import (
    ControlledTaskAuthority,
    load_visible_task,
    materialize_controlled_task,
)
from cernora_reference_workflow.runtime_policy import (
    PI_RUNTIME_ENVIRONMENT,
    PI_RUNTIME_INSTALLATION,
    PI_VERSION,
    RUNTIME_CLEANUP_RECEIPT,
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
)
from tests.unit.test_controlled_experiment_spec import valid_payload


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _proxy_environment() -> dict[str, str]:
    return {
        "CERNORA_HTTP_PROXY": "http://proxy.example:18080",
        "CERNORA_HTTPS_PROXY": "http://proxy.example:18080",
        "CERNORA_ALL_PROXY": "socks5://proxy.example:11080",
    }


def _auth_file(root: Path) -> Path:
    path = root / "auth.json"
    path.write_text(json.dumps({"tokens": {"access_token": "unit-secret-marker"}}))
    return path


def _split_model_info(model: str) -> dict[str, str | None]:
    if "/" in model:
        provider, name = model.split("/", 1)
        # Harbor 0.16.1 with the pinned pi Runtime reports the provider split.
        return {"name": name, "provider": provider}
    return {"name": model, "provider": None}


def _spec(
    task: ControlledTaskAuthority,
    *,
    configuration_id: str = "baseline",
    prompt: str = "Repair the project using the frozen task evidence.",
    candidate_failure_binding: dict[str, str] | None = None,
    profile_tasks: tuple[ControlledTaskAuthority, ...] | None = None,
) -> ControlledExperimentSpecV2:
    payload = valid_payload(
        case_id=task.case.case_id,
        configuration_id=configuration_id,
        prompt=prompt,
    )
    if candidate_failure_binding is not None:
        prompt_source = materialize_authority_source(
            "treatment-prompt",
            cast(
                JsonValue,
                {"selected_failure": candidate_failure_binding, "text": prompt},
            ),
        )
        payload["prompt_source"] = prompt_source.model_dump(mode="json")
        payload["prompt_sha256"] = prompt_source.source_sha256
    runtime_source = materialize_authority_source(
        "runtime",
        cast(
            JsonValue,
            {
                "pi_environment_sha256": sha256_bytes(canonical_json_bytes(PI_RUNTIME_ENVIRONMENT)),
                "pi_runtime_installation": PI_RUNTIME_INSTALLATION,
                "policy": RUNTIME_POLICY,
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
    task_authority_source = materialize_authority_source(
        "controlled-task-authority", cast(JsonValue, task.model_dump(mode="json"))
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
    profile = build_controlled_profile_authority(profile_tasks or (task,))
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
        task_authority_id=task.authority_id,
        task_authority_sha256=task_authority_source.source_sha256,
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
        "authority_id": task.authority_id,
        "authority_sha256": task_authority_source.source_sha256,
        "authority_source": task_authority_source.model_dump(mode="json"),
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


def _harbor_agent_config(spec: ControlledExperimentSpecV2) -> dict[str, object]:
    return {
        "name": "cernora_reference_workflow.runtime_agent:TelemetryDisabledPi",
        "import_path": None,
        "model_name": spec.runtime.model,
        "n_concurrent": None,
        "concurrency_group": None,
        "skills": [],
        "override_timeout_sec": None,
        "override_setup_timeout_sec": None,
        "max_timeout_sec": None,
        "extra_allowed_hosts": [],
        "include_logs": [],
        "exclude_logs": [],
        "kwargs": {
            "thinking": spec.runtime.reasoning_effort,
            "version": spec.runtime.version,
        },
        "env": {},
        "mcp_servers": [],
    }


def _harbor_environment_config(spec: ControlledExperimentSpecV2) -> dict[str, object]:
    return {
        "type": "docker",
        "import_path": None,
        "force_build": False,
        "delete": True,
        "cpu_enforcement_policy": "auto",
        "memory_enforcement_policy": "auto",
        "override_cpus": spec.limits.cpu_millis // 1000,
        "override_memory_mb": spec.limits.memory_mebibytes,
        "override_storage_mb": None,
        "override_gpus": None,
        "override_tpu": None,
        "mounts": None,
        "extra_docker_compose": [],
        "env": {},
        "kwargs": {},
        "extra_allowed_hosts": [],
    }


def _harbor_verifier_config() -> dict[str, object]:
    return {
        "disable": False,
        "env": {},
        "exclude_logs": [],
        "import_path": None,
        "include_logs": [],
        "kwargs": {},
        "max_timeout_sec": None,
        "override_timeout_sec": None,
    }


def test_actual_harbor_cli_job_config_matches_argv_authority(tmp_path: Path) -> None:
    """Exercise Harbor's real parser without starting a job or provider call."""

    from uuid import UUID

    import typer
    from click import Group
    from harbor.cli.jobs import jobs_app
    from harbor.models.trial.config import TrialConfig

    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    request = _request(spec)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    task_root = tmp_path / task.case.case_id
    task_root.mkdir()
    job_root = tmp_path / "jobs"
    job_root.mkdir()
    job_name = "offline-config-authority"
    live_attempt_module._materialize_task(request, task, task_root)
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=tmp_path,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        cli_validator=lambda _: None,
    )
    command = executor._command(
        request,
        task_root,
        job_root,
        job_name,
        {
            "HTTP_PROXY": _proxy_environment()["CERNORA_HTTP_PROXY"],
            "HTTPS_PROXY": _proxy_environment()["CERNORA_HTTPS_PROXY"],
            "ALL_PROXY": _proxy_environment()["CERNORA_ALL_PROXY"],
            "NO_PROXY": "localhost,127.0.0.1",
        },
    )
    group = cast(Group, typer.main.get_command(jobs_app))
    config = group.commands["start"].main([*command[2:], "--init"], standalone_mode=False)

    assert config is not None
    actual = config.model_dump(mode="json")
    trial = TrialConfig(
        task=config.tasks[0],
        trials_dir=config.jobs_dir / config.job_name,
        install_only=config.install_only,
        timeout_multiplier=config.timeout_multiplier,
        agent_timeout_multiplier=config.agent_timeout_multiplier,
        verifier_timeout_multiplier=config.verifier_timeout_multiplier,
        agent_setup_timeout_multiplier=config.agent_setup_timeout_multiplier,
        environment_build_timeout_multiplier=config.environment_build_timeout_multiplier,
        agent=config.agents[0],
        environment=config.environment,
        verifier=config.verifier,
        artifacts=config.artifacts,
        extra_instruction_paths=config.extra_instruction_paths,
        job_id=UUID(int=2),
    ).model_dump(mode="json")
    assert trial["agent"] == actual["agents"][0]
    assert trial["task"] == actual["tasks"][0]
    proxy_environment = {
        "HTTP_PROXY": _proxy_environment()["CERNORA_HTTP_PROXY"],
        "HTTPS_PROXY": _proxy_environment()["CERNORA_HTTPS_PROXY"],
        "ALL_PROXY": _proxy_environment()["CERNORA_ALL_PROXY"],
        "NO_PROXY": "localhost,127.0.0.1",
    }
    live_attempt_module._validate_job_config(
        actual,
        request,
        task_root=task_root,
        job_root=job_root,
        job_name=job_name,
        proxy_environment=proxy_environment,
    )
    exclusions = actual["retry"]["exclude_exceptions"]
    exclusions.reverse()
    live_attempt_module._validate_job_config(
        actual,
        request,
        task_root=task_root,
        job_root=job_root,
        job_name=job_name,
        proxy_environment=proxy_environment,
    )
    exclusions[-1] = exclusions[0]
    with pytest.raises(LiveAttemptError, match="retry exclusions drift"):
        live_attempt_module._validate_job_config(
            actual,
            request,
            task_root=task_root,
            job_root=job_root,
            job_name=job_name,
            proxy_environment=proxy_environment,
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
        disk_free: object = None,
        safe_stop_free_bytes: int | None = None,
    ) -> SubprocessResult:
        del cwd, deadline_monotonic, timeout_seconds, disk_free, safe_stop_free_bytes
        self.commands.append(command)
        assert "DEEPSEEK_API_KEY" not in environment
        assert environment["HTTP_PROXY"] == _proxy_environment()["CERNORA_HTTP_PROXY"]
        assert environment["HTTPS_PROXY"] == _proxy_environment()["CERNORA_HTTPS_PROXY"]
        assert environment["ALL_PROXY"] == _proxy_environment()["CERNORA_ALL_PROXY"]
        assert environment["NO_PROXY"] == "localhost,127.0.0.1"
        assert set(environment).issubset(
            {
                "PATH",
                "HOME",
                "TMPDIR",
                "LANG",
                "LC_ALL",
                "PI_AUTH_JSON_PATH",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "DOCKER_CONTEXT",
                "DOCKER_HOST",
                "NO_COLOR",
                "NO_PROXY",
                "REQUESTS_CA_BUNDLE",
                "SSL_CERT_FILE",
            }
        )
        task_root = Path(command[command.index("-p") + 1])
        job_root = Path(command[command.index("-o") + 1])
        job_name = command[command.index("--job-name") + 1]
        verifier = job_root / job_name / "trial-1" / "verifier"
        verifier.mkdir(parents=True)
        kwargs = cast(
            dict[str, object],
            dict(
                value.split("=", 1)
                for value in command
                if "=" in value and value.split("=", 1)[0] in {"version", "thinking"}
            ),
        )
        environment_config = _harbor_environment_config(self.spec)
        job_agent_config = _harbor_agent_config(self.spec)
        trial_agent_config = _harbor_agent_config(self.spec)
        assert job_agent_config["kwargs"] == kwargs
        config: dict[str, object] = {
            "job_name": job_name,
            "jobs_dir": str(job_root),
            "n_attempts": 1,
            "install_only": False,
            "timeout_multiplier": 1.0,
            "agent_timeout_multiplier": 1.0,
            "verifier_timeout_multiplier": None,
            "agent_setup_timeout_multiplier": 4.0,
            "environment_build_timeout_multiplier": None,
            "debug": False,
            "n_concurrent_trials": 1,
            "quiet": False,
            "retry": {
                "max_retries": 0,
                "min_wait_sec": 1.0,
                "max_wait_sec": 60.0,
                "wait_multiplier": 1.0,
                "include_exceptions": None,
                "exclude_exceptions": [
                    "AgentTimeoutError",
                    "ApiUsageLimitError",
                    "VerifierOutputParseError",
                    "RewardFileEmptyError",
                    "RewardFileNotFoundError",
                    "VerifierTimeoutError",
                ],
            },
            "environment": environment_config,
            "verifier": _harbor_verifier_config(),
            "metrics": [],
            "agents": [job_agent_config],
            "datasets": [],
            "tasks": [
                {
                    "path": str(task_root),
                    "git_url": None,
                    "git_commit_id": None,
                    "name": None,
                    "ref": None,
                    "overwrite": False,
                    "download_dir": None,
                    "source": None,
                }
            ],
            "artifacts": [],
            "extra_instruction_paths": [],
        }
        job = verifier.parents[1]
        (job / "config.json").write_bytes(canonical_json_bytes(config))
        agent = verifier.parent / "agent"
        agent.mkdir()
        (agent / "pi-environment.json").write_bytes(canonical_json_bytes(PI_RUNTIME_ENVIRONMENT))
        (agent / "runtime-policy.json").write_bytes(canonical_json_bytes(RUNTIME_POLICY))
        (agent / "runtime-cleanup.json").write_bytes(canonical_json_bytes(RUNTIME_CLEANUP_RECEIPT))
        (agent / "pi-version.txt").write_text(f"{PI_VERSION}\n", encoding="utf-8")
        candidate = verifier / "candidate"
        candidate.mkdir()
        for item in (*self.task.workspace_files, *self.task.test_files):
            path = candidate / item.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.content())
        before = {
            item.path: item.sha256 for item in (*self.task.workspace_files, *self.task.test_files)
        }
        stdout = b"behavioral verifier output\n"
        stderr = b""
        (verifier / "stdout.txt").write_bytes(stdout)
        (verifier / "stderr.txt").write_bytes(stderr)
        receipt = {
            "allowed_paths": list(self.task.allowed_paths),
            "authority_workspace": {item.path: item.sha256 for item in self.task.workspace_files},
            "candidate_before": before,
            "candidate_after": before,
            "duration_milliseconds": 1,
            "exit_code": 1,
            "schema_version": "cernora.reference.controlled-verifier-execution/v1",
            "stderr_sha256": sha256_bytes(stderr),
            "stdout_sha256": sha256_bytes(stdout),
            "test_command": list(self.task.test_command),
            "test_files": {item.path: item.sha256 for item in self.task.test_files},
            "working_directory": "candidate",
        }
        (verifier / "execution-receipt.json").write_bytes(canonical_json_bytes(receipt))
        (verifier / "exit-code.txt").write_text("1\n", encoding="ascii")
        from dirhash import dirhash  # type: ignore[import-untyped]

        trial_config: dict[str, object] = {
            "task": {
                "path": str(task_root),
                "git_url": None,
                "git_commit_id": None,
                "name": None,
                "ref": None,
                "overwrite": False,
                "download_dir": None,
                "source": None,
            },
            "trial_name": "trial-1",
            "trials_dir": str(job),
            "install_only": False,
            "timeout_multiplier": 1.0,
            "agent_timeout_multiplier": 1.0,
            "verifier_timeout_multiplier": None,
            "agent_setup_timeout_multiplier": 4.0,
            "environment_build_timeout_multiplier": None,
            "agent": trial_agent_config,
            "environment": environment_config,
            "verifier": _harbor_verifier_config(),
            "artifacts": [],
            "extra_instruction_paths": [],
            "job_id": "00000000-0000-0000-0000-000000000002",
        }
        result = {
            "id": "00000000-0000-0000-0000-000000000001",
            "task_name": self.task.case.case_id,
            "trial_name": "trial-1",
            "trial_uri": verifier.parent.resolve().as_uri(),
            "task_id": {"path": str(task_root)},
            "source": None,
            "task_checksum": dirhash(task_root, "sha256"),
            "config": trial_config,
            "agent_info": {
                "name": "pi",
                "version": self.spec.runtime.version,
                "model_info": _split_model_info(self.spec.runtime.model),
            },
            "agent_result": {},
            "verifier_result": {},
            "exception_info": None,
            "started_at": "2026-08-27T00:00:00Z",
            "finished_at": "2026-08-27T00:00:01Z",
            "environment_setup": None,
            "agent_setup": None,
            "agent_execution": {
                "started_at": "2026-08-27T00:00:00Z",
                "finished_at": "2026-08-27T00:00:01Z",
            },
            "verifier": None,
            "step_results": None,
        }
        (verifier.parent / "result.json").write_bytes(canonical_json_bytes(result))
        return SubprocessResult(
            status="exited",
            exit_code=0,
            stdout=b"provider output is not portable evidence",
            stderr=b"status 503 provider service unavailable",
            started_monotonic=0.0,
            finished_monotonic=1.0,
            receipt_sha256=digest("process"),
        )


class FakeContainers:
    def __init__(self) -> None:
        self.cleaned: list[tuple[str, str | None]] = []

    def snapshot(self) -> ContainerSnapshot:
        return ContainerSnapshot(time.time(), {})

    def cleanup_new(
        self,
        before: ContainerSnapshot,
        *,
        expected_image_id: str,
        job_name: str,
        trial_name: str | None,
    ) -> tuple[str, ...]:
        del before, expected_image_id
        self.cleaned.append((job_name, trial_name))
        return ()


class ResultDriftProcess(FakeProcess):
    def __init__(
        self,
        task: ControlledTaskAuthority,
        spec: ControlledExperimentSpecV2,
        mutation: str,
    ) -> None:
        super().__init__(task, spec)
        self.mutation = mutation

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline_monotonic: float,
        timeout_seconds: int,
        disk_free: object = None,
        safe_stop_free_bytes: int | None = None,
    ) -> SubprocessResult:
        process = super().__call__(
            command,
            cwd=cwd,
            environment=environment,
            deadline_monotonic=deadline_monotonic,
            timeout_seconds=timeout_seconds,
            disk_free=disk_free,
            safe_stop_free_bytes=safe_stop_free_bytes,
        )
        job_root = Path(command[command.index("-o") + 1])
        job_name = command[command.index("--job-name") + 1]
        result_path = job_root / job_name / "trial-1" / "result.json"
        payload = json.loads(result_path.read_bytes())
        if self.mutation == "agent":
            payload["agent_info"]["name"] = "unexpected-agent"
        else:
            payload["config"]["environment"]["delete"] = False
        result_path.write_bytes(canonical_json_bytes(payload))
        return process


def test_runner_interrupt_rediscovers_trial_before_container_cleanup(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)

    class InterruptAfterTrial(FakeProcess):
        def __call__(self, *args: object, **kwargs: object) -> SubprocessResult:
            super().__call__(*args, **kwargs)  # type: ignore[arg-type]
            raise KeyboardInterrupt

    repository = tmp_path / "repo"
    repository.mkdir()
    evaluation = tmp_path / "evaluations"
    evaluation.mkdir()
    containers = FakeContainers()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository,
        tasks=(task,),
        evaluation_root=evaluation,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=InterruptAfterTrial(task, spec),
        container_controller=containers,
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    with pytest.raises(KeyboardInterrupt):
        executor(_request(spec, trial="interrupt-cleanup"))

    assert len(containers.cleaned) == 1
    assert containers.cleaned[0][1] == "trial-1"


def test_ambiguous_trial_hint_still_attempts_cleanup_for_each_trial(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)

    def ambiguous_runner(
        command: tuple[str, ...],
        **_kwargs: object,
    ) -> SubprocessResult:
        job_root = Path(command[command.index("-o") + 1])
        job_name = command[command.index("--job-name") + 1]
        (job_root / job_name / "trial-a").mkdir(parents=True)
        (job_root / job_name / "trial-b").mkdir()
        raise KeyboardInterrupt

    repository = tmp_path / "repo"
    repository.mkdir()
    evaluation = tmp_path / "evaluations"
    evaluation.mkdir()
    containers = FakeContainers()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository,
        tasks=(task,),
        evaluation_root=evaluation,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=ambiguous_runner,
        container_controller=containers,
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    with pytest.raises(LiveAttemptError, match="more than one Trial"):
        executor(_request(spec, trial="ambiguous-cleanup"))

    assert {trial_name for _, trial_name in containers.cleaned} == {"trial-a", "trial-b"}


class TransientResultProcess(FakeProcess):
    def __call__(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline_monotonic: float,
        timeout_seconds: int,
        disk_free: object = None,
        safe_stop_free_bytes: int | None = None,
    ) -> SubprocessResult:
        process = super().__call__(
            command,
            cwd=cwd,
            environment=environment,
            deadline_monotonic=deadline_monotonic,
            timeout_seconds=timeout_seconds,
            disk_free=disk_free,
            safe_stop_free_bytes=safe_stop_free_bytes,
        )
        job_root = Path(command[command.index("-o") + 1])
        job_name = command[command.index("--job-name") + 1]
        result_path = job_root / job_name / "trial-1" / "result.json"
        payload = json.loads(result_path.read_bytes())
        payload["agent_result"] = None
        payload["verifier_result"] = None
        payload["exception_info"] = {
            "exception_message": "provider status 503 service unavailable",
            "exception_traceback": "provider call failed",
            "exception_type": "NonZeroAgentExitCodeError",
            "occurred_at": "2026-08-27T00:00:00Z",
        }
        result_path.write_bytes(canonical_json_bytes(payload))
        return replace(process, exit_code=1, stderr=b"ordinary stderr")


class AgentTimeoutResultProcess(FakeProcess):
    """Replay Harbor's closed result shape after its Agent phase times out."""

    def __init__(
        self,
        task: ControlledTaskAuthority,
        spec: ControlledExperimentSpecV2,
        mutation: str | None = None,
    ) -> None:
        super().__init__(task, spec)
        self.mutation = mutation

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline_monotonic: float,
        timeout_seconds: int,
        disk_free: object = None,
        safe_stop_free_bytes: int | None = None,
    ) -> SubprocessResult:
        process = super().__call__(
            command,
            cwd=cwd,
            environment=environment,
            deadline_monotonic=deadline_monotonic,
            timeout_seconds=timeout_seconds,
            disk_free=disk_free,
            safe_stop_free_bytes=safe_stop_free_bytes,
        )
        job_root = Path(command[command.index("-o") + 1])
        job_name = command[command.index("--job-name") + 1]
        result_path = job_root / job_name / "trial-1" / "result.json"
        payload = json.loads(result_path.read_bytes())
        payload["agent_result"] = {
            "cost_usd": None,
            "metadata": None,
            "n_cache_tokens": None,
            "n_input_tokens": None,
            "n_output_tokens": None,
            "rollout_details": None,
        }
        payload["verifier_result"] = {"rewards": {"reward": 0.0}}
        exception_at = (
            datetime.fromisoformat("2026-08-27T00:05:15Z")
            .astimezone()
            .replace(tzinfo=None)
            .isoformat()
        )
        payload["exception_info"] = {
            "exception_message": "Agent execution timed out after 300.0 seconds",
            "exception_traceback": (
                "Traceback (most recent call last):\n"
                "AgentTimeoutError: Agent execution timed out after 300.0 seconds\n"
            ),
            "exception_type": "AgentTimeoutError",
            "occurred_at": exception_at,
        }
        payload["agent_execution"] = {
            "started_at": "2026-08-27T00:00:00Z",
            "finished_at": "2026-08-27T00:05:15Z",
        }
        payload["verifier"] = {
            "started_at": "2026-08-27T00:05:15Z",
            "finished_at": "2026-08-27T00:05:16Z",
        }
        if self.mutation == "partial-agent-metrics":
            payload["agent_result"].update(
                {
                    "cost_usd": 0.25,
                    "n_cache_tokens": 12,
                    "n_input_tokens": 34,
                    "n_output_tokens": 56,
                }
            )
        elif self.mutation == "missing-agent-result":
            payload["agent_result"] = None
        elif self.mutation == "invalid-agent-result":
            payload["agent_result"] = "not-an-AgentContext"
        elif self.mutation == "extra-agent-result-field":
            payload["agent_result"]["unexpected"] = None
        elif self.mutation == "partial-agent-token-counts":
            payload["agent_result"]["n_input_tokens"] = 34
        elif self.mutation == "negative-agent-cost":
            payload["agent_result"].update(
                {
                    "cost_usd": -0.25,
                    "n_cache_tokens": 12,
                    "n_input_tokens": 34,
                    "n_output_tokens": 56,
                }
            )
        elif self.mutation == "cost-only-agent-result":
            payload["agent_result"]["cost_usd"] = 0.25
        elif self.mutation == "verifier-result":
            payload["verifier_result"] = {}
        elif self.mutation == "missing-verifier-result":
            payload["verifier_result"] = None
        elif self.mutation == "missing-agent-execution":
            payload["agent_execution"] = None
        elif self.mutation == "incomplete-agent-execution":
            payload["agent_execution"]["finished_at"] = None
        elif self.mutation == "reversed-agent-execution":
            payload["agent_execution"]["finished_at"] = "2026-08-26T23:59:59Z"
        elif self.mutation == "missing-verifier-timing":
            payload["verifier"] = None
        elif self.mutation == "reversed-verifier-timing":
            payload["verifier"]["finished_at"] = "2026-08-27T00:05:14Z"
        elif self.mutation == "malformed-verifier-timing":
            payload["verifier"]["finished_at"] = "not-a-timestamp"
        elif self.mutation == "naive-agent-timing":
            payload["agent_execution"]["started_at"] = "2026-08-27T00:00:00"
        elif self.mutation == "exception-before-agent-finished":
            payload["exception_info"]["occurred_at"] = (
                datetime.fromisoformat("2026-08-27T00:05:14Z")
                .astimezone()
                .replace(tzinfo=None)
                .isoformat()
            )
        elif self.mutation == "exception-after-verifier-started":
            payload["exception_info"]["occurred_at"] = (
                datetime.fromisoformat("2026-08-27T00:05:16Z")
                .astimezone()
                .replace(tzinfo=None)
                .isoformat()
            )
        elif self.mutation == "aware-exception-timing":
            payload["exception_info"]["occurred_at"] = "2026-08-27T00:05:15Z"
        elif self.mutation == "extreme-naive-exception-timing":
            payload["exception_info"]["occurred_at"] = "0001-01-01T00:00:00"
        elif self.mutation == "wrong-timeout-message":
            payload["exception_info"]["exception_message"] = (
                "Agent execution timed out after 299.0 seconds"
            )
        elif self.mutation == "wrong-timeout-traceback":
            payload["exception_info"]["exception_traceback"] = (
                "AgentTimeoutError: unrelated timeout"
            )
        elif self.mutation == "agent-exceeds-envelope":
            payload["agent_execution"]["finished_at"] = "2026-08-27T00:06:01Z"
            payload["exception_info"]["occurred_at"] = (
                datetime.fromisoformat("2026-08-27T00:06:01Z")
                .astimezone()
                .replace(tzinfo=None)
                .isoformat()
            )
            payload["verifier"]["started_at"] = "2026-08-27T00:06:01Z"
            payload["verifier"]["finished_at"] = "2026-08-27T00:06:02Z"
        result_path.write_bytes(canonical_json_bytes(payload))
        finished_monotonic = 316.0
        if self.mutation == "process-exceeds-envelope":
            finished_monotonic = 361.0
        elif self.mutation == "process-shorter-than-phases":
            finished_monotonic = 315.0
        return replace(
            process,
            started_monotonic=0.0,
            finished_monotonic=finished_monotonic,
            stderr=b"ordinary Harbor timeout diagnostics",
        )


@pytest.mark.parametrize(
    "mutation",
    (None, "partial-agent-metrics", "missing-verifier-result"),
)
def test_closed_harbor_agent_timeout_remains_a_timeout_lifecycle(
    tmp_path: Path,
    mutation: str | None,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=AgentTimeoutResultProcess(task, spec, mutation),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
        close_unusable_runtime_evidence=True,
        attempt_envelope_grace_seconds=60,
    )

    suffix = mutation or "empty-agent-context"
    attempt = executor(_request(spec, trial=f"closed-agent-timeout-{suffix}"))

    assert attempt.retry_eligible is False
    assert attempt.lifecycle is not None
    assert attempt.lifecycle.category == "timed_out", executor.diagnostic_code
    assert attempt.lifecycle.source_state == "timed-out"
    assert attempt.resources.duration_milliseconds == 316_000
    assert attempt.runtime_observation is None
    assert attempt.repair_result is None
    assert executor.diagnostic_code == "agent-timeout-evidence-accepted"


@pytest.mark.parametrize(
    ("mutation", "diagnostic_code"),
    (
        ("missing-agent-result", "agent-timeout-agent-result"),
        ("missing-agent-execution", "agent-timeout-agent-timing-shape"),
        ("verifier-result", "agent-timeout-verifier-result"),
        ("missing-verifier-timing", "agent-timeout-verifier-timing-shape"),
        ("wrong-timeout-message", "agent-timeout-message"),
        ("wrong-timeout-traceback", "agent-timeout-traceback"),
        ("exception-before-agent-finished", "agent-timeout-timezone-order"),
        ("agent-exceeds-envelope", "agent-timeout-duration-bound"),
    ),
)
def test_closed_timeout_rejection_retains_only_a_fixed_diagnostic_code(
    tmp_path: Path,
    mutation: str,
    diagnostic_code: str,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=AgentTimeoutResultProcess(task, spec, mutation),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
        close_unusable_runtime_evidence=True,
        attempt_envelope_grace_seconds=60,
    )

    attempt = executor(_request(spec, trial=f"diagnostic-code-{mutation}"))

    assert attempt.lifecycle is not None
    assert attempt.lifecycle.category == "runtime_pre_terminal_failure"
    assert executor.diagnostic_code == diagnostic_code


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-agent-result",
        "invalid-agent-result",
        "extra-agent-result-field",
        "partial-agent-token-counts",
        "negative-agent-cost",
        "cost-only-agent-result",
        "verifier-result",
        "missing-verifier-result",
        "missing-agent-execution",
        "incomplete-agent-execution",
        "reversed-agent-execution",
        "missing-verifier-timing",
        "reversed-verifier-timing",
        "malformed-verifier-timing",
        "naive-agent-timing",
        "exception-before-agent-finished",
        "exception-after-verifier-started",
        "aware-exception-timing",
        "extreme-naive-exception-timing",
        "wrong-timeout-message",
        "wrong-timeout-traceback",
        "agent-exceeds-envelope",
        "process-exceeds-envelope",
        "process-shorter-than-phases",
    ),
)
def test_agent_timeout_requires_exact_harbor_phase_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=AgentTimeoutResultProcess(task, spec, mutation),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    with pytest.raises(LiveAttemptError, match="Agent timeout result contradicts"):
        executor(_request(spec, trial=f"malformed-agent-timeout-{mutation}"))


def test_container_selection_removes_only_exact_new_goal_containers() -> None:
    captured = 1_000.0
    image = "a" * 64
    existing = ContainerRecord("existing", f"sha256:{image}", 900.0, {"project": "m4-job"})
    before = ContainerSnapshot(captured, {existing.container_id: existing})
    exact = ContainerRecord(
        "exact", f"sha256:{image}", 1_001.0, {"com.docker.compose.project": "trial-1__env"}
    )
    unrelated = ContainerRecord(
        "unrelated", f"sha256:{image}", 1_001.0, {"com.docker.compose.project": "other"}
    )
    near_match = ContainerRecord(
        "near-match",
        f"sha256:{image}",
        1_001.0,
        {"com.docker.compose.project": "prefix-trial-1-suffix"},
    )
    wrong_image = ContainerRecord(
        "wrong-image", f"sha256:{'b' * 64}", 1_001.0, {"project": "m4-job"}
    )
    stale = ContainerRecord("stale", f"sha256:{image}", 800.0, {"project": "m4-job"})
    after = ContainerSnapshot(
        1_002.0,
        {
            item.container_id: item
            for item in (existing, exact, near_match, unrelated, wrong_image, stale)
        },
    )

    assert attributable_container_ids(
        before,
        after,
        expected_image_id=image,
        job_name="m4-job",
        trial_name="trial-1",
    ) == ("exact",)


def test_live_executor_observes_authorities_and_emits_real_core_package(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    process = FakeProcess(task, spec)
    containers = FakeContainers()
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        ambient_environment=lambda: {
            "PATH": "/usr/bin",
            "HOME": "/example/home",
            "DEEPSEEK_API_KEY": "ambient-must-not-leak",
            "HTTP_PROXY": "http://ambient.example:19090",
        },
        process_runner=process,
        container_controller=containers,
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    attempt = executor(_request(spec))

    assert attempt.evaluation is not None
    assert attempt.repair_result is not None and not attempt.repair_result.passed
    assert attempt.retry_eligible is False
    assert attempt.runtime_observation is not None
    attempt.runtime_observation.verify(spec)
    portable = canonical_json_bytes(attempt.model_dump(mode="json"))
    assert b"18080" not in portable
    assert b"/private/" not in portable
    assert containers.cleaned and process.commands
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


@pytest.mark.parametrize("mutation", ("agent", "environment", "delete", "memory"))
def test_live_executor_rejects_actual_harbor_argv_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    process = FakeProcess(task, spec)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
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
            if mutation == "agent":
                command[command.index("-a") + 1] = "unexpected.agent:Drift"
            elif mutation == "environment":
                command[command.index("-e") + 1] = "local"
            elif mutation == "delete":
                command.remove("--delete")
            else:
                command[command.index("--override-memory-mb") + 1] = "2048"
            return tuple(command)

    executor = DriftedExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=process,
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    with pytest.raises(LiveAttemptError, match="drifts|delete/yes"):
        executor(_request(spec, trial="drift"))


@pytest.mark.parametrize("mutation", ("agent", "environment"))
def test_live_executor_rejects_actual_harbor_result_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=ResultDriftProcess(task, spec, mutation),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    expected_message = (
        "actual Harbor result" if mutation == "agent" else "actual Harbor Trial config"
    )
    with pytest.raises(LiveAttemptError, match=expected_message):
        executor(_request(spec, trial=f"result-{mutation}-drift"))


def test_pilot_policy_closes_unusable_runtime_evidence_without_observation(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=ResultDriftProcess(task, spec, "agent"),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
        close_unusable_runtime_evidence=True,
    )

    attempt = executor(_request(spec, trial="pilot-unusable-evidence"))

    assert attempt.retry_eligible is False
    assert attempt.lifecycle is not None
    assert attempt.lifecycle.category == "runtime_pre_terminal_failure"
    assert attempt.runtime_observation is None
    assert attempt.repair_result is None
    assert executor.diagnostic_code == "preterminal-structure-rejected"


def test_live_executor_keeps_private_output_failure_fail_closed(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    proxy = _proxy_environment()

    class PrivateOutputProcess(FakeProcess):
        def __call__(
            self,
            command: tuple[str, ...],
            *,
            cwd: Path,
            environment: Mapping[str, str],
            deadline_monotonic: float,
            timeout_seconds: int,
            disk_free: object = None,
            safe_stop_free_bytes: int | None = None,
        ) -> SubprocessResult:
            process = super().__call__(
                command,
                cwd=cwd,
                environment=environment,
                deadline_monotonic=deadline_monotonic,
                timeout_seconds=timeout_seconds,
                disk_free=disk_free,
                safe_stop_free_bytes=safe_stop_free_bytes,
            )
            return replace(process, stdout=proxy["CERNORA_HTTP_PROXY"].encode())

    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=proxy,
        process_runner=PrivateOutputProcess(task, spec),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
        close_unusable_runtime_evidence=True,
    )

    with pytest.raises(LiveAttemptError, match="private value") as raised:
        executor(_request(spec, trial="private-output"))

    assert raised.value.diagnostic_code == "private-value-in-process-output"
    assert executor.diagnostic_code == "private-value-in-process-output"


def test_private_value_scan_fails_closed_with_fixed_code_when_tree_is_unreadable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "job"
    root.mkdir()
    oversized = root / "session-transcript.json"
    oversized.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    process = SubprocessResult(
        status="exited",
        exit_code=0,
        stdout=b"",
        stderr=b"",
        started_monotonic=0.0,
        finished_monotonic=1.0,
        receipt_sha256=sha256_bytes(b"oversized-scan"),
    )

    with pytest.raises(LiveAttemptError) as raised:
        live_attempt_module._assert_private_values_absent(
            root,
            auth_path=_auth_file(tmp_path),
            markers=(),
            proxy_environment={},
            explicit_proxy_endpoints=(),
            process=process,
        )

    assert raised.value.diagnostic_code == "scan-tree-unreadable"


def test_unverified_start_failure_does_not_receive_retry(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    observed_timeouts: list[int] = []

    def start_failure(
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline_monotonic: float,
        timeout_seconds: int,
        disk_free: object = None,
        safe_stop_free_bytes: int | None = None,
    ) -> SubprocessResult:
        observed_timeouts.append(timeout_seconds)
        del (
            command,
            cwd,
            environment,
            deadline_monotonic,
            disk_free,
            safe_stop_free_bytes,
        )
        return SubprocessResult(
            status="start_failure",
            exit_code=None,
            stdout=b"",
            stderr=b"",
            started_monotonic=0.0,
            finished_monotonic=0.0,
            receipt_sha256=digest("start-failure"),
        )

    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=start_failure,
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
        attempt_envelope_grace_seconds=60,
    )

    attempt = executor(_request(spec, trial="eligible-retry"))

    assert attempt.retry_eligible is False
    assert attempt.lifecycle is not None
    assert observed_timeouts == [360]
    assert attempt.lifecycle.category == "runtime_pre_terminal_failure"


def test_live_executor_requires_strict_preterminal_result_for_transient_retry(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=TransientResultProcess(task, spec),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    attempt = executor(_request(spec, trial="transient-result"))

    assert attempt.retry_eligible is True
    assert attempt.lifecycle is not None
    assert attempt.lifecycle.category == "transient_provider_pre_terminal"


@pytest.mark.parametrize(
    "mutation",
    (
        "wrong-task",
        "wrong-checksum",
        "wrong-config",
        "incomplete",
        "reidentified",
        "malformed",
        "remote-source",
        "task-git-url",
        "extra-nested-config",
        "job-retry-drift",
        "job-extra-nested-config",
        "job-bool-int",
        "job-int-float",
        "trial-bool-int",
        "trial-int-float",
    ),
)
def test_unverified_preterminal_result_never_receives_retry(
    tmp_path: Path,
    mutation: str,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)

    class MutatedTransientProcess(TransientResultProcess):
        def __call__(
            self,
            command: tuple[str, ...],
            *,
            cwd: Path,
            environment: Mapping[str, str],
            deadline_monotonic: float,
            timeout_seconds: int,
            disk_free: object = None,
            safe_stop_free_bytes: int | None = None,
        ) -> SubprocessResult:
            process = super().__call__(
                command,
                cwd=cwd,
                environment=environment,
                deadline_monotonic=deadline_monotonic,
                timeout_seconds=timeout_seconds,
                disk_free=disk_free,
                safe_stop_free_bytes=safe_stop_free_bytes,
            )
            job_root = Path(command[command.index("-o") + 1])
            job_name = command[command.index("--job-name") + 1]
            if mutation.startswith("job-"):
                config_path = job_root / job_name / "config.json"
                config = json.loads(config_path.read_bytes())
                if mutation == "job-retry-drift":
                    config["retry"]["max_retries"] = 1
                elif mutation == "job-bool-int":
                    config["n_attempts"] = True
                elif mutation == "job-int-float":
                    config["timeout_multiplier"] = 1
                else:
                    config["environment"]["unexpected"] = True
                config_path.write_bytes(canonical_json_bytes(config))
                return process
            result_path = job_root / job_name / "trial-1" / "result.json"
            payload = json.loads(result_path.read_bytes())
            if mutation == "wrong-task":
                payload["task_name"] = "different-task"
            elif mutation == "wrong-checksum":
                payload["task_checksum"] = digest("different-task")
            elif mutation == "wrong-config":
                payload["config"]["agent"]["model_name"] = "different-model"
            elif mutation == "incomplete":
                payload.pop("task_id")
            elif mutation == "reidentified":
                payload["trial_name"] = "different-trial"
            elif mutation == "malformed":
                payload["exception_info"].pop("occurred_at")
            elif mutation == "remote-source":
                payload["source"] = "remote-dataset"
            elif mutation == "task-git-url":
                payload["config"]["task"]["git_url"] = "https://invalid.example/repo.git"
            elif mutation == "trial-bool-int":
                payload["config"]["install_only"] = 0
            elif mutation == "trial-int-float":
                payload["config"]["timeout_multiplier"] = 1
            else:
                payload["config"]["agent"]["unexpected"] = True
            result_path.write_bytes(canonical_json_bytes(payload))
            return process

    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=MutatedTransientProcess(task, spec),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
        close_unusable_runtime_evidence=True,
    )

    attempt = executor(_request(spec, trial=f"unverified-{mutation}"))

    assert attempt.retry_eligible is False
    assert attempt.lifecycle is not None
    assert attempt.lifecycle.category == "runtime_pre_terminal_failure"
    assert attempt.runtime_observation is None
    assert attempt.repair_result is None
    trial_config_mutations = {
        "extra-nested-config",
        "task-git-url",
        "trial-bool-int",
        "trial-int-float",
        "wrong-config",
    }
    assert executor.diagnostic_code == (
        "trial-tree-rejected"
        if mutation == "reidentified"
        else (
            "job-config-authority-rejected"
            if mutation.startswith("job-")
            else (
                "trial-config-authority-rejected"
                if mutation in trial_config_mutations
                else "preterminal-structure-rejected"
            )
        )
    )


def test_loose_transient_token_match_is_not_retry_eligible(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)

    class LooseTokenProcess(TransientResultProcess):
        def __call__(
            self,
            command: tuple[str, ...],
            *,
            cwd: Path,
            environment: Mapping[str, str],
            deadline_monotonic: float,
            timeout_seconds: int,
            disk_free: object = None,
            safe_stop_free_bytes: int | None = None,
        ) -> SubprocessResult:
            process = super().__call__(
                command,
                cwd=cwd,
                environment=environment,
                deadline_monotonic=deadline_monotonic,
                timeout_seconds=timeout_seconds,
                disk_free=disk_free,
                safe_stop_free_bytes=safe_stop_free_bytes,
            )
            job_root = Path(command[command.index("-o") + 1])
            job_name = command[command.index("--job-name") + 1]
            result_path = job_root / job_name / "trial-1" / "result.json"
            payload = json.loads(result_path.read_bytes())
            payload["exception_info"]["exception_message"] = (
                "provider incident 15003 upstream marker only"
            )
            result_path.write_bytes(canonical_json_bytes(payload))
            return process

    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=LooseTokenProcess(task, spec),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    attempt = executor(_request(spec, trial="loose-transient-token"))

    assert attempt.retry_eligible is False
    assert attempt.lifecycle is not None
    assert attempt.lifecycle.category == "runtime_pre_terminal_failure"


def test_behavioral_result_never_retries_despite_transient_stderr(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=FakeProcess(task, spec),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    attempt = executor(_request(spec, trial="behavioral-no-retry"))

    assert attempt.retry_eligible is False
    assert attempt.lifecycle is None


@pytest.mark.parametrize("mutation", ("command", "workspace"))
def test_reidentified_task_command_or_workspace_drift_is_rejected(
    tmp_path: Path,
    mutation: str,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    payload = task.model_dump(mode="json", exclude={"authority_id"})
    case = payload["case"]
    assert isinstance(case, dict)
    case_input = case["input"]
    assert isinstance(case_input, dict)
    parameters = case_input["parameters"]
    assert isinstance(parameters, dict)
    if mutation == "command":
        command = [*task.test_command, "--unexpected"]
        payload["test_command"] = command
        parameters["test_command"] = command
    else:
        workspace = payload["workspace_files"]
        assert isinstance(workspace, list) and isinstance(workspace[0], dict)
        changed = b"def merge_intervals(values):\n    return []\n"
        workspace[0]["content_base64"] = base64.b64encode(changed).decode("ascii")
        workspace[0]["size_bytes"] = len(changed)
        workspace[0]["sha256"] = sha256_bytes(changed)
    drifted = materialize_controlled_task(payload)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(drifted,),
        evaluation_root=evaluation_root,
        auth_file=tmp_path / "not-read.json",
        proxy_environment=_proxy_environment(),
        process_runner=FakeProcess(drifted, spec),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    with pytest.raises(LiveAttemptError, match="exact controlled task"):
        executor(_request(spec, trial=f"task-{mutation}-drift"))


def test_wrong_or_missing_image_workspace_is_rejected_before_auth_read(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()

    def wrong_workspace(
        specification: ControlledExperimentSpecV2,
        authority: ControlledTaskAuthority,
    ) -> str:
        del specification, authority
        raise LiveAttemptError("pinned task image workspace contradicts task authority")

    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=tmp_path / "not-read.json",
        proxy_environment=_proxy_environment(),
        process_runner=FakeProcess(task, spec),
        container_controller=FakeContainers(),
        cli_validator=lambda _: None,
        image_verifier=wrong_workspace,
    )

    with pytest.raises(LiveAttemptError, match="image workspace"):
        executor(_request(spec, trial="wrong-image-workspace"))


def test_generated_verifier_executes_exact_authority_command_and_all_files(
    tmp_path: Path,
) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    task_root = tmp_path / task.case.case_id
    task_root.mkdir()
    live_attempt_module._materialize_task(_request(spec), task, task_root)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    for item in task.workspace_files:
        path = candidate / item.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item.content())
    logs = tmp_path / "logs"

    completed = subprocess.run(
        (
            sys.executable,
            str(task_root / "tests/verifier_driver.py"),
            "--candidate",
            str(candidate),
            "--authority",
            str(task_root / "tests/verifier-authority.json"),
            "--test-root",
            str(task_root / "tests/authority"),
            "--logs",
            str(logs),
        ),
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
        },
    )

    assert completed.returncode == 0
    receipt = json.loads((logs / "execution-receipt.json").read_bytes())
    assert tuple(receipt["test_command"]) == task.test_command
    assert receipt["test_files"] == {item.path: item.sha256 for item in task.test_files}
    assert receipt["authority_workspace"] == {
        item.path: item.sha256 for item in task.workspace_files
    }
    assert receipt["exit_code"] == 1


def test_generated_verifier_rejects_added_test_file(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    task_root = tmp_path / task.case.case_id
    task_root.mkdir()
    live_attempt_module._materialize_task(_request(spec), task, task_root)
    unexpected = task_root / "tests/authority/tests/unexpected.py"
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_text("raise AssertionError\n", encoding="utf-8")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    for item in task.workspace_files:
        path = candidate / item.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item.content())

    completed = subprocess.run(
        (
            sys.executable,
            str(task_root / "tests/verifier_driver.py"),
            "--candidate",
            str(candidate),
            "--authority",
            str(task_root / "tests/verifier-authority.json"),
            "--test-root",
            str(task_root / "tests/authority"),
            "--logs",
            str(tmp_path / "logs"),
        ),
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
        },
    )

    assert completed.returncode != 0
    assert not (tmp_path / "logs/execution-receipt.json").exists()


@pytest.mark.parametrize("repository_kind", ("current", "main", "linked", "core"))
def test_auth_is_rejected_inside_every_git_worktree(
    tmp_path: Path,
    repository_kind: str,
) -> None:
    worktree = tmp_path / repository_kind
    worktree.mkdir()
    marker = worktree / ".git"
    if repository_kind == "linked":
        marker.write_text("gitdir: /outside/example\n", encoding="utf-8")
    else:
        marker.mkdir()
    auth = _auth_file(worktree)

    with pytest.raises(LiveAttemptError, match="outside every Git worktree"):
        live_attempt_module._stable_auth_markers(auth, tmp_path / "unrelated")


def test_auth_hardlink_is_rejected_even_when_external_name_is_outside_repo(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    tracked = _auth_file(repository)
    external = tmp_path / "external-auth.json"
    os.link(tracked, external)

    with pytest.raises(LiveAttemptError, match="regular file"):
        live_attempt_module._stable_auth_markers(external, repository)


def test_executor_private_scan_uses_only_selected_proxy_endpoints(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    repository = tmp_path / "repo"
    repository.mkdir()
    evaluation = tmp_path / "evaluation"
    evaluation.mkdir()
    executor = ControlledHarborAttemptExecutor(
        repository_root=repository,
        tasks=(task,),
        evaluation_root=evaluation,
        auth_file=_auth_file(tmp_path),
        proxy_environment={
            "http_proxy": "http://proxy.example:18080",
            "https_proxy": "http://proxy.example:18080",
            "all_proxy": "socks5://proxy.example:11080",
            "NO_COLOR": "1",
            "SHLVL": "2",
            "TERM": "dumb",
        },
        cli_validator=lambda _: None,
        attempt_envelope_grace_seconds=60,
    )

    assert executor._proxy_environment == {
        "HTTP_PROXY": "http://proxy.example:18080",
        "HTTPS_PROXY": "http://proxy.example:18080",
        "ALL_PROXY": "socks5://proxy.example:11080",
        "NO_PROXY": "localhost,127.0.0.1",
    }
    assert executor._explicit_proxy_endpoints == ()
    assert executor._attempt_envelope_grace_seconds == 60


@pytest.mark.parametrize("location", ("stdout", "stderr", "artifact"))
def test_private_proxy_endpoint_detection_is_fail_closed_and_value_free(
    tmp_path: Path,
    location: str,
) -> None:
    proxy = {
        "HTTP_PROXY": "http://proxy.example:18080",
        "HTTPS_PROXY": "http://proxy.example:18080",
        "ALL_PROXY": "socks5://proxy.example:11080",
        "NO_PROXY": "localhost,127.0.0.1,::1",
    }
    root = tmp_path / "job"
    root.mkdir()
    auth = _auth_file(tmp_path)
    (root / "result.json").write_bytes(b"{}")
    stdout = proxy["HTTP_PROXY"].encode() if location == "stdout" else b""
    stderr = proxy["ALL_PROXY"].encode() if location == "stderr" else b""
    if location == "artifact":
        (root / "config.json").write_text(proxy["HTTPS_PROXY"], encoding="utf-8")
    process = SubprocessResult(
        status="exited",
        exit_code=0,
        stdout=stdout,
        stderr=stderr,
        started_monotonic=0.0,
        finished_monotonic=1.0,
        receipt_sha256=digest("private-scan"),
    )

    with pytest.raises(LiveAttemptError) as raised:
        live_attempt_module._assert_private_values_absent(
            root,
            auth_path=auth,
            markers=(b"unit-secret-marker",),
            proxy_environment=proxy,
            explicit_proxy_endpoints=tuple(proxy.values()),
            process=process,
        )

    assert "18080" not in str(raised.value)
    assert "11080" not in str(raised.value)


def test_nonsecret_no_proxy_value_does_not_trigger_private_scan(tmp_path: Path) -> None:
    root = tmp_path / "job"
    root.mkdir()
    (root / "result.json").write_text('{"bypass":"localhost,127.0.0.1"}', encoding="utf-8")
    process = SubprocessResult(
        status="exited",
        exit_code=0,
        stdout=b"localhost,127.0.0.1",
        stderr=b"",
        started_monotonic=0.0,
        finished_monotonic=1.0,
        receipt_sha256=digest("nonsecret-no-proxy"),
    )

    live_attempt_module._assert_private_values_absent(
        root,
        auth_path=_auth_file(tmp_path),
        markers=(),
        proxy_environment={"NO_PROXY": "localhost,127.0.0.1"},
        explicit_proxy_endpoints=(),
        process=process,
    )


@pytest.mark.parametrize("path_kind", ("symlink", "canonicalized"))
def test_private_scan_rejects_resolved_auth_path_without_echoing_it(
    tmp_path: Path,
    path_kind: str,
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    target = _auth_file(private)
    if path_kind == "symlink":
        auth_path = tmp_path / "auth-link.json"
        auth_path.symlink_to(target)
    else:
        auth_path = private / "nested" / ".." / "auth.json"
        (private / "nested").mkdir()
    root = tmp_path / "job"
    root.mkdir()
    (root / "result.json").write_bytes(str(target.resolve()).encode("utf-8"))
    process = SubprocessResult(
        status="exited",
        exit_code=1,
        stdout=b"",
        stderr=b"",
        started_monotonic=0.0,
        finished_monotonic=1.0,
        receipt_sha256=digest(f"resolved-auth:{path_kind}"),
    )

    with pytest.raises(LiveAttemptError) as raised:
        live_attempt_module._assert_private_values_absent(
            root,
            auth_path=auth_path,
            markers=(),
            proxy_environment={},
            explicit_proxy_endpoints=(),
            process=process,
        )

    assert str(auth_path) not in str(raised.value)
    assert str(target.resolve()) not in str(raised.value)


def test_active_disk_safe_stop_cleans_exact_runtime_containers(tmp_path: Path) -> None:
    task = load_visible_task(Path("examples/m4-visible/dev-interval-merge"))
    spec = _spec(task)
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    evaluation_root = tmp_path / "evaluations"
    evaluation_root.mkdir()
    containers = FakeContainers()

    def disk_stop(
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline_monotonic: float,
        timeout_seconds: int,
        disk_free: object = None,
        safe_stop_free_bytes: int | None = None,
    ) -> SubprocessResult:
        del command, cwd, environment, deadline_monotonic, timeout_seconds
        assert disk_free is not None
        assert safe_stop_free_bytes == 8 * 1024**3
        return SubprocessResult(
            status="safe_stopped",
            exit_code=None,
            stdout=b"",
            stderr=b"",
            started_monotonic=0.0,
            finished_monotonic=1.0,
            receipt_sha256=digest("disk-stop"),
        )

    executor = ControlledHarborAttemptExecutor(
        repository_root=repository_root,
        tasks=(task,),
        evaluation_root=evaluation_root,
        auth_file=_auth_file(tmp_path),
        proxy_environment=_proxy_environment(),
        process_runner=disk_stop,
        container_controller=containers,
        cli_validator=lambda _: None,
        image_verifier=lambda value, _: value.container.image.rsplit("@sha256:", 1)[1],
    )

    with pytest.raises(ControlledActiveSafeStop, match="disk_safe_stop"):
        executor(_request(spec, trial="disk-safe-stop"))

    assert len(containers.cleaned) == 1


def test_installed_harbor_help_matches_production_command_surface() -> None:
    validate_installed_harbor_cli(Path(sys.executable).with_name("harbor"))


def test_harbor_cli_rejects_wrong_installed_distribution_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    help_text = " ".join(live_attempt_module._REQUIRED_HARBOR_OPTIONS)

    def completed(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        output = "0.16.0\n" if "-c" in command else help_text
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(subprocess, "run", completed)

    with pytest.raises(LiveAttemptError, match="qualified 0.16.1"):
        validate_installed_harbor_cli(Path("/qualified/bin/harbor"))
