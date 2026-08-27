"""Production Harbor/Codex Attempt executor for controlled task authorities."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from cernora import BatchAttemptResources, BatchLifecycleRecord

from cernora_reference_workflow.common import (
    ContractError,
    canonical_content_id,
    canonical_json_bytes,
    closed_regular_tree,
    read_regular_file_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.controlled_evaluation import (
    RepairResultRecord,
    materialize_repair_result,
)
from cernora_reference_workflow.controlled_execution import (
    ControlledAttempt,
    ControlledAttemptRequest,
    materialize_controlled_attempt,
)
from cernora_reference_workflow.controlled_experiment_spec import ControlledExperimentSpecV2
from cernora_reference_workflow.controlled_profile import evaluate_repair_result_package
from cernora_reference_workflow.controlled_runtime import (
    RuntimeAuthorityObservation,
    SubprocessResult,
    run_subprocess_until,
    runtime_invocation_sha256,
)
from cernora_reference_workflow.controlled_task import ControlledTaskAuthority
from cernora_reference_workflow.runtime_policy import (
    RUNTIME_CLEANUP_RECEIPT,
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
    TELEMETRY_CONFIG_TOML,
    resolve_provider_proxy_environment,
)

AGENT_IMPORT = "cernora_reference_workflow.runtime_agent:TelemetryDisabledCodex"

ProcessRunner = Callable[..., SubprocessResult]
EnvironmentProvider = Callable[[], Mapping[str, str]]
CleanupVerifier = Callable[[str], None]
CliValidator = Callable[[Path], None]
ImageVerifier = Callable[[ControlledExperimentSpecV2], str]

_REQUIRED_HARBOR_OPTIONS = (
    "--agent",
    "--agent-kwarg",
    "--delete",
    "--env",
    "--job-name",
    "--jobs-dir",
    "--max-retries",
    "--model",
    "--n-attempts",
    "--n-concurrent",
    "--override-cpus",
    "--override-memory-mb",
    "--path",
    "--yes",
)


class LiveAttemptError(ContractError):
    """The live Runtime could not produce a closed controlled Attempt."""


class _ProcessRunner(Protocol):
    def __call__(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline_monotonic: float,
        timeout_seconds: int,
    ) -> SubprocessResult: ...


def validate_installed_harbor_cli(executable: Path) -> None:
    """Require the exact real option surface used by the production argv."""

    result = subprocess.run(
        (str(executable), "run", "--help"),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "COLUMNS": "240", "NO_COLOR": "1"},
    )
    if result.returncode != 0 or any(
        option not in result.stdout for option in _REQUIRED_HARBOR_OPTIONS
    ):
        raise LiveAttemptError("installed Harbor CLI does not match the qualified 0.16.1 surface")


def _verify_local_task_image(specification: ControlledExperimentSpecV2) -> str:
    image = specification.container.image
    platform = specification.container.platform
    expected = f"sha256:{image.rsplit('@sha256:', 1)[1]}"
    result = subprocess.run(
        ("docker", "image", "inspect", expected, "--format", "{{.Id}} {{.Os}}/{{.Architecture}}"),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or result.stdout.strip() != f"{expected} {platform}":
        raise LiveAttemptError("local task image identity or platform is unavailable")
    return expected.removeprefix("sha256:")


def compose_controlled_instruction(
    task: ControlledTaskAuthority,
    request: ControlledAttemptRequest,
) -> str:
    """Compose only task-owned prose and selected canonical Treatment sources."""

    spec = request.specification

    def text(source: object, label: str) -> str:
        payload = getattr(source, "payload", None)
        allowed = {"text"} if label == "Instruction" else {"selected_failure", "text"}
        if (
            not isinstance(payload, dict)
            or set(payload) not in ({"text"}, allowed)
            or not isinstance(payload["text"], str)
        ):
            raise LiveAttemptError(f"{label} authority has an invalid strict text payload")
        if "selected_failure" in payload:
            selected = payload["selected_failure"]
            if not isinstance(selected, dict) or set(selected) != {
                "code",
                "profile_id",
                "profile_version",
            }:
                raise LiveAttemptError(f"{label} selected_failure binding is invalid")
        return payload["text"]

    sections = (
        task.case.input.prompt,
        text(spec.prompt_source, "Prompt"),
        text(spec.instruction_source, "Instruction"),
    )
    return "\n\n".join(sections) + "\n"


def _runtime_observation(
    request: ControlledAttemptRequest,
    task: ControlledTaskAuthority,
    command: tuple[str, ...],
    task_root: Path,
) -> RuntimeAuthorityObservation:
    """Parse the actual invocation and derive the authority observation."""

    spec = request.specification
    agent_kwargs = {
        command[index + 1] for index, value in enumerate(command[:-1]) if value == "--ak"
    }
    task_config = tomllib.loads(read_regular_file_bytes(task_root / "task.toml").decode("utf-8"))
    environment = task_config.get("environment")
    if not isinstance(environment, dict):
        raise LiveAttemptError("materialized Harbor task omits its environment authority")
    task_image = environment.get("docker_image")
    expected_image = f"sha256:{spec.container.image.rsplit('@sha256:', 1)[1]}"
    if (
        command[command.index("-m") + 1] != spec.runtime.model
        or f"version={spec.runtime.version}" not in agent_kwargs
        or f"reasoning_effort={spec.runtime.reasoning_effort}" not in agent_kwargs
        or command[command.index("--override-cpus") + 1] != str(spec.limits.cpu_millis // 1000)
        or command[command.index("--override-memory-mb") + 1] != str(spec.limits.memory_mebibytes)
        or command[command.index("-n") + 1] != "1"
        or command[command.index("-k") + 1] != "1"
        or command[command.index("-r") + 1] != "0"
        or task_image != expected_image
    ):
        raise LiveAttemptError("actual Harbor invocation drifts from Experiment authority")
    if task.case.case_id != spec.task.task_id or task.case_sha256 != spec.task.content_sha256:
        raise LiveAttemptError("actual task authority drifts from Experiment Case")
    projection = spec.core_projection()
    payload: dict[str, object] = {
        "schema_version": "cernora.reference.runtime-authority-observation/v1",
        "experiment_id": spec.experiment_id,
        "runtime_configuration_sha256": spec.runtime.configuration_sha256,
        "prompt_sha256": spec.prompt_source.source_sha256,
        "instruction_sha256": spec.instruction_source.source_sha256,
        "task_source_sha256": spec.task.task_source.source_sha256,
        "task_image_sha256": expected_image.removeprefix("sha256:"),
        "invocation_sha256": runtime_invocation_sha256(spec),
        "runtime_version_sha256": projection.runtime_version_sha256,
        "model_sha256": projection.model_sha256,
        "tool_schema_sha256": projection.tool_schema_sha256,
        "generation_configuration_sha256": projection.generation_configuration_sha256,
        "timeout_sha256": projection.timeout_sha256,
        "resources_sha256": projection.resources_sha256,
        "retry_policy_sha256": projection.retry_policy_sha256,
        "dataset_sha256": projection.dataset_sha256,
        "profile_sha256": projection.profile_sha256,
        "evaluation_policy_sha256": projection.evaluation_policy_sha256,
        "report_contract_sha256": projection.report_contract_sha256,
        "statistical_plan_sha256": projection.statistical_plan_sha256,
    }
    payload["observation_id"] = canonical_content_id(payload, excluded=frozenset())
    observation = RuntimeAuthorityObservation.model_validate(payload)
    observation.verify(spec)
    return observation


def _default_cleanup(job_name: str) -> None:
    result = subprocess.run(
        ("docker", "ps", "-a", "--format", "{{.Names}}"),
        check=True,
        capture_output=True,
        text=True,
    )
    normalized = job_name.lower().replace("_", "-")
    if any(normalized in item.lower().replace("_", "-") for item in result.stdout.splitlines()):
        raise LiveAttemptError("Harbor container cleanup was not exact")


def _lifecycle_attempt(
    request: ControlledAttemptRequest,
    process: SubprocessResult,
    *,
    category: str,
    retry_eligible: bool,
) -> ControlledAttempt:
    lifecycle = BatchLifecycleRecord(
        schema_version="agent.evaluator.batch-lifecycle/v1",
        category=category,  # type: ignore[arg-type]
        retry_eligible=retry_eligible,
        source_state=category.replace("_", "-"),
        receipt_sha256=process.receipt_sha256,
    )
    source_attempt_id = canonical_content_id(
        {
            "ordinal": request.ordinal,
            "process_receipt_sha256": process.receipt_sha256,
            "trial_id": request.trial_id,
        },
        excluded=frozenset(),
    )
    return materialize_controlled_attempt(
        {
            "schema_version": "cernora.reference.controlled-attempt/v1",
            "trial_id": request.trial_id,
            "ordinal": request.ordinal,
            "predecessor_attempt_id": request.predecessor_attempt_id,
            "source_attempt_id": source_attempt_id,
            "source_manifest_sha256": process.receipt_sha256,
            "retry_eligible": retry_eligible,
            "resources": BatchAttemptResources(
                duration_milliseconds=max(
                    0, int((process.finished_monotonic - process.started_monotonic) * 1000)
                )
            ).model_dump(mode="json"),
            "runtime_observation": None,
            "repair_result": None,
            "evaluation": None,
            "lifecycle": lifecycle.model_dump(mode="json"),
        }
    )


class ControlledHarborAttemptExecutor:
    """One serial qualified executor; secrets exist only in the child environment."""

    def __init__(
        self,
        *,
        repository_root: Path,
        tasks: tuple[ControlledTaskAuthority, ...],
        evaluation_root: Path,
        environment_provider: EnvironmentProvider,
        process_runner: _ProcessRunner = run_subprocess_until,
        cleanup_verifier: CleanupVerifier = _default_cleanup,
        cli_validator: CliValidator = validate_installed_harbor_cli,
        image_verifier: ImageVerifier = _verify_local_task_image,
    ) -> None:
        self._repository_root = repository_root
        self._tasks = {item.case.case_id: item for item in tasks}
        self._task_suite = tasks
        self._evaluation_root = evaluation_root
        self._environment_provider = environment_provider
        self._process_runner = process_runner
        self._cleanup_verifier = cleanup_verifier
        self._image_verifier = image_verifier
        cli_validator(repository_root / ".venv/bin/harbor")

    @property
    def enforces_hard_deadline(self) -> bool:
        return True

    def _command(
        self,
        request: ControlledAttemptRequest,
        task_root: Path,
        job_root: Path,
        job_name: str,
        proxy_environment: Mapping[str, str],
    ) -> tuple[str, ...]:
        spec = request.specification
        if spec.limits.cpu_millis < 1000 or spec.limits.cpu_millis % 1000:
            raise LiveAttemptError("Harbor CPU override cannot exactly represent Experiment limits")
        command = [
            str(self._repository_root / ".venv/bin/harbor"),
            "run",
            "-p",
            str(task_root),
            "-a",
            AGENT_IMPORT,
            "-m",
            spec.runtime.model,
            "-e",
            "docker",
            "--ak",
            f"version={spec.runtime.version}",
            "--ak",
            f"reasoning_effort={spec.runtime.reasoning_effort}",
            "--ak",
            "reasoning_summary=none",
            "--ak",
            "web_search=disabled",
            "--ak",
            "strict_config=true",
            "--agent-setup-timeout-multiplier",
            "4",
            "--agent-timeout-multiplier",
            "1",
            "--override-cpus",
            str(spec.limits.cpu_millis // 1000),
            "--override-memory-mb",
            str(spec.limits.memory_mebibytes),
            "--delete",
            "-o",
            str(job_root),
            "--job-name",
            job_name,
            "-n",
            "1",
            "-k",
            "1",
            "-r",
            "0",
            "--yes",
        ]
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            command.extend(("--ae", f"{name}={proxy_environment[name]}"))
        return tuple(command)

    def _materialize_task(
        self,
        request: ControlledAttemptRequest,
        task: ControlledTaskAuthority,
        task_root: Path,
    ) -> None:
        spec = request.specification
        environment = task_root / "environment"
        tests = task_root / "tests"
        workspace = environment / "workspace"
        environment.mkdir()
        tests.mkdir()
        workspace.mkdir()
        for item in task.workspace_files:
            path = workspace / item.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.content())
        for item in task.test_files:
            relative = item.path.removeprefix("tests/")
            path = tests / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.content())
        instruction = compose_controlled_instruction(task, request)
        (task_root / "instruction.md").write_text(instruction, encoding="utf-8")
        image_digest = spec.container.image.rsplit("@sha256:", 1)[1]
        task_toml = (
            'schema_version = "1.3"\n\n'
            "[metadata]\n"
            'author_name = "Cernora contributors"\n'
            'author_email = "noreply@example.invalid"\n'
            'difficulty = "hard"\n'
            'category = "software-engineering"\n'
            'tags = ["python", "repair", "deterministic"]\n\n'
            "[verifier]\n"
            f"timeout_sec = {float(spec.limits.timeout_seconds)!r}\n\n"
            "[agent]\n"
            f"timeout_sec = {float(spec.limits.timeout_seconds)!r}\n\n"
            "[environment]\n"
            "build_timeout_sec = 600.0\n"
            f'docker_image = "sha256:{image_digest}"\n'
            'workdir = "/workspace"\n'
            'network_mode = "public"\n'
        )
        (task_root / "task.toml").write_text(task_toml, encoding="utf-8")
        dockerfile = (
            f"FROM {spec.container.build_base_image}\n"
            "WORKDIR /workspace\n"
            "COPY workspace/ /workspace/\n"
        )
        (environment / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        test_target = task.allowed_paths[0]
        test_script = (
            "#!/bin/sh\n"
            "set -u\n"
            "mkdir -p /logs/verifier/candidate\n"
            "cp -a /workspace/. /logs/verifier/candidate/\n"
            "started_milliseconds=$(date +%s%3N)\n"
            "cd /logs/verifier/candidate\n"
            f"python /tests/{shlex.quote(task.test_files[0].path.removeprefix('tests/'))} "
            f"{shlex.quote(test_target)} > /logs/verifier/stdout.txt "
            "2> /logs/verifier/stderr.txt\n"
            "status=$?\n"
            "finished_milliseconds=$(date +%s%3N)\n"
            "printf '%s\\n' \"$status\" > /logs/verifier/exit-code.txt\n"
            "printf '%s\\n' \"$((finished_milliseconds - started_milliseconds))\" "
            "> /logs/verifier/duration-milliseconds.txt\n"
            "if [ \"$status\" -eq 0 ]; then printf '1\\n'; else printf '0\\n'; fi "
            "> /logs/verifier/reward.txt\n"
            "exit 0\n"
        )
        test_sh = tests / "test.sh"
        test_sh.write_text(test_script, encoding="utf-8")
        test_sh.chmod(0o755)

    def _result_from_job(
        self,
        request: ControlledAttemptRequest,
        task: ControlledTaskAuthority,
        job_root: Path,
        job_name: str,
    ) -> RepairResultRecord:
        job = job_root / job_name
        if not job.is_dir() or job.is_symlink():
            raise LiveAttemptError("Harbor did not publish one real job directory")
        trials = tuple(
            item
            for item in job.iterdir()
            if item.is_dir() and not item.is_symlink() and (item / "result.json").is_file()
        )
        if len(trials) != 1:
            raise LiveAttemptError("Harbor job does not contain exactly one closed Trial")
        verifier = trials[0] / "verifier"
        agent = trials[0] / "agent"
        runtime_artifacts = {
            "effective-config.toml": TELEMETRY_CONFIG_TOML.encode("utf-8"),
            "runtime-policy.json": canonical_json_bytes(RUNTIME_POLICY),
            "runtime-cleanup.json": canonical_json_bytes(RUNTIME_CLEANUP_RECEIPT),
        }
        for name, expected in runtime_artifacts.items():
            if read_regular_file_bytes(agent / name) != expected:
                raise LiveAttemptError("Harbor Runtime artifact contradicts the pinned authority")
        features = read_regular_file_bytes(agent / "effective-features.txt").decode("utf-8")
        feature_rows = {tuple(line.split()) for line in features.splitlines()}
        if not {
            ("plugins", "stable", "false"),
            ("unified_exec", "stable", "true"),
        }.issubset(feature_rows):
            raise LiveAttemptError("Harbor Runtime features do not prove the pinned policy")
        if request.specification.runtime.configuration_sha256 != RUNTIME_CONFIGURATION_SHA256:
            raise LiveAttemptError("Experiment Runtime configuration is not the pinned authority")
        try:
            exit_code = int(read_regular_file_bytes(verifier / "exit-code.txt").strip())
        except (OSError, ValueError) as exc:
            raise LiveAttemptError("Harbor verifier exit receipt is invalid") from exc
        candidate = closed_regular_tree(verifier / "candidate")
        initial = {item.path: item.sha256 for item in task.workspace_files}
        observed = {
            path: sha256_bytes(read_regular_file_bytes(source))
            for path, source in candidate.items()
        }
        changed = tuple(
            sorted(
                path
                for path in set(initial) | set(observed)
                if initial.get(path) != observed.get(path)
            )
        )
        protected = task.test_source_sha256
        return materialize_repair_result(
            {
                "schema_version": "cernora.reference.repair-result/v1",
                "case_id": task.case.case_id,
                "result_record_version": "agent.evaluator.result-record/v1",
                "test_authority_sha256": request.specification.test_runner.authority_sha256,
                "test_plan_sha256": request.specification.test_runner.test_plan_sha256,
                "test_source_sha256": request.specification.test_runner.test_source_sha256,
                "termination": "exited",
                "exit_code": exit_code,
                "checks": [
                    {
                        "check_id": "frozen-verifier",
                        "failure_code": task.failure_code,
                        "passed": exit_code == 0,
                    }
                ],
                "allowed_paths": list(task.allowed_paths),
                "changed_paths": list(changed),
                "protected_paths": list(task.protected_paths),
                "protected_path_receipt": {
                    "before_sha256": protected,
                    "after_sha256": protected,
                    "unchanged": True,
                },
            }
        )

    def __call__(self, request: ControlledAttemptRequest) -> ControlledAttempt:
        task = self._tasks.get(request.slot.case_id)
        if task is None:
            raise LiveAttemptError("selected Case has no controlled task authority")
        temporary = Path(tempfile.mkdtemp(prefix="cernora-m4-attempt-", dir=self._evaluation_root))
        job_name = f"m4-{request.trial_id[:12]}-{request.ordinal}"
        try:
            task_root = temporary / "task"
            job_root = temporary / "job"
            task_root.mkdir()
            job_root.mkdir()
            self._materialize_task(request, task, task_root)
            observed_image_sha256 = self._image_verifier(request.specification)
            if (
                observed_image_sha256
                != request.specification.container.image.rsplit("@sha256:", 1)[1]
            ):
                raise LiveAttemptError("observed local task image contradicts Experiment authority")
            environment = dict(self._environment_provider())
            proxy_environment = resolve_provider_proxy_environment(environment)
            command = self._command(
                request,
                task_root,
                job_root,
                job_name,
                proxy_environment,
            )
            observation = _runtime_observation(request, task, command, task_root)
            process = self._process_runner(
                command,
                cwd=self._repository_root,
                environment=environment,
                deadline_monotonic=request.global_deadline_monotonic,
                timeout_seconds=request.specification.limits.timeout_seconds,
            )
            self._cleanup_verifier(job_name)
            if process.status == "start_failure":
                return _lifecycle_attempt(
                    request,
                    process,
                    category="infrastructure_start_failure",
                    retry_eligible=True,
                )
            if process.status in {"timed_out", "output_limit"}:
                return _lifecycle_attempt(
                    request,
                    process,
                    category="runtime_pre_terminal_failure",
                    retry_eligible=False,
                )
            if process.exit_code != 0:
                transient = any(
                    marker in process.stderr.lower()
                    for marker in (b"status 429", b"status 502", b"status 503", b"status 504")
                )
                return _lifecycle_attempt(
                    request,
                    process,
                    category=(
                        "transient_provider_pre_terminal"
                        if transient
                        else "runtime_pre_terminal_failure"
                    ),
                    retry_eligible=transient,
                )
            result = self._result_from_job(request, task, job_root, job_name)
            raw = canonical_json_bytes(result.model_dump(mode="json"))
            source_attempt_id = canonical_content_id(
                {
                    "process_receipt_sha256": process.receipt_sha256,
                    "result_id": result.result_id,
                    "trial_id": request.trial_id,
                },
                excluded=frozenset(),
            )
            evaluation_output = (
                self._evaluation_root / request.trial_id / f"{request.ordinal:04d}-evaluation"
            )
            evaluation_output.parent.mkdir(parents=True, exist_ok=True)
            package = evaluate_repair_result_package(
                task=task,
                tasks=self._task_suite,
                result=result,
                source_attempt_id=source_attempt_id,
                output=evaluation_output,
            )
            return materialize_controlled_attempt(
                {
                    "schema_version": "cernora.reference.controlled-attempt/v1",
                    "trial_id": request.trial_id,
                    "ordinal": request.ordinal,
                    "predecessor_attempt_id": request.predecessor_attempt_id,
                    "source_attempt_id": source_attempt_id,
                    "source_manifest_sha256": canonical_content_id(
                        {
                            "process_receipt_sha256": process.receipt_sha256,
                            "repair_result_sha256": sha256_bytes(raw),
                            "runtime_observation_id": observation.observation_id,
                        },
                        excluded=frozenset(),
                    ),
                    "retry_eligible": False,
                    "resources": BatchAttemptResources(
                        duration_milliseconds=max(
                            0,
                            int((process.finished_monotonic - process.started_monotonic) * 1000),
                        )
                    ).model_dump(mode="json"),
                    "runtime_observation": observation.model_dump(mode="json"),
                    "repair_result": result.model_dump(mode="json"),
                    "evaluation": package.model_dump(mode="python"),
                    "lifecycle": None,
                }
            )
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def inherited_ephemeral_environment() -> Mapping[str, str]:
    """Return a child-only environment; callers must inject auth just before spawn."""

    return dict(os.environ)


__all__ = [
    "ControlledHarborAttemptExecutor",
    "LiveAttemptError",
    "compose_controlled_instruction",
    "inherited_ephemeral_environment",
    "validate_installed_harbor_cli",
]
