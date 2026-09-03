"""Pinned Harbor pi integration with an observable telemetry-off policy."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import override

from harbor.agents.installed.base import with_prompt_template
from harbor.agents.installed.pi import Pi
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.pi_trajectory import write_pi_trajectory
from cernora_reference_workflow.runtime_policy import (
    PI_CONFIG_DIR,
    PI_RUNTIME_ENVIRONMENT,
    PI_SECRETS_DIR,
    PI_VERSION,
    PREINSTALLED_PI_CHECK_COMMAND,
    RUNTIME_CLEANUP_RECEIPT,
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
)

_PI_OFFLINE_ENVIRONMENT = {
    "PI_CODING_AGENT_DIR": PI_CONFIG_DIR,
    "PI_OFFLINE": "1",
    "PI_SKIP_VERSION_CHECK": "1",
    "PI_TELEMETRY": "0",
}
_PI_DISCOVERY_DISABLE_ARGUMENTS = (
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
)


class TelemetryDisabledPi(Pi):  # type: ignore[misc]
    """Harbor 0.16.1 pi pinned to the offline preinstalled Runtime base."""

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        """Fail closed unless the exact native Runtime is already in the pinned image."""

        await self.exec_as_agent(
            environment,
            command=PREINSTALLED_PI_CHECK_COMMAND,
        )

    @override
    def get_version_command(self) -> str:
        return "pi --version"

    def _resolve_auth_path(self) -> Path:
        """Require the one explicit external auth file selected by the operator."""

        explicit = self._get_env("PI_AUTH_JSON_PATH")
        if not explicit:
            raise ContractError("PI_AUTH_JSON_PATH must name the explicit external auth file")
        return Path(explicit)

    @override
    @with_prompt_template  # type: ignore[misc]
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name or "/" not in self.model_name:
            raise ContractError("pi model name must be in provider/model form")
        provider, model = self.model_name.split("/", 1)

        remote_home = PI_CONFIG_DIR
        remote_secrets = PI_SECRETS_DIR
        remote_auth = f"{remote_secrets}/auth.json"
        agent_dir = EnvironmentPaths.agent_dir.as_posix()
        sessions_dir = f"{agent_dir}/sessions"

        environment_receipt = shlex.quote(
            canonical_json_bytes(PI_RUNTIME_ENVIRONMENT).decode("utf-8")
        )
        policy_receipt = shlex.quote(canonical_json_bytes(RUNTIME_POLICY).decode("utf-8"))

        # Prepare directories before uploading auth: the upload target must exist
        # so the fast compose copy path is taken instead of a slow tar fallback
        # that can burn the whole agent time budget on short-timeout trials.
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"mkdir -p {shlex.quote(remote_home)} {shlex.quote(remote_secrets)} "
                f"{shlex.quote(agent_dir)} {shlex.quote(sessions_dir)}"
            ),
            env=dict(_PI_OFFLINE_ENVIRONMENT),
        )
        auth_path = self._resolve_auth_path()
        await environment.upload_file(auth_path, remote_auth)
        if environment.default_user is not None:
            await self.exec_as_root(
                environment,
                command=f"chown {environment.default_user} {shlex.quote(remote_auth)}",
            )
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"ln -sf {shlex.quote(remote_auth)} {shlex.quote(remote_home)}/auth.json; "
                f"printf %s {environment_receipt} > "
                f"{shlex.quote(agent_dir)}/pi-environment.json; "
                f"printf %s {policy_receipt} > {shlex.quote(agent_dir)}/runtime-policy.json; "
                f"pi --version > {shlex.quote(agent_dir)}/pi-version.txt; "
                f"test -s {shlex.quote(agent_dir)}/pi-version.txt"
            ),
            env=dict(_PI_OFFLINE_ENVIRONMENT),
        )
        try:
            cli_flags = self.build_cli_flags()
            cli_flags_arg = f"{cli_flags} " if cli_flags else ""
            disable_arguments = " ".join(_PI_DISCOVERY_DISABLE_ARGUMENTS)
            await self.exec_as_agent(
                environment,
                command=(
                    "pi --print --mode json "
                    f"--provider {shlex.quote(provider)} "
                    f"--model {shlex.quote(model)} "
                    f"{cli_flags_arg}"
                    f"--session-dir {shlex.quote(sessions_dir)} "
                    f"{disable_arguments} "
                    f"{shlex.quote(instruction)} "
                    '2>&1 </dev/null | grep -v \'"type":"message_update"\' | '
                    f"stdbuf -oL tee {shlex.quote(agent_dir)}/pi.txt"
                ),
                env=dict(_PI_OFFLINE_ENVIRONMENT),
            )
        finally:
            cleanup_receipt = shlex.quote(
                canonical_json_bytes(RUNTIME_CLEANUP_RECEIPT).decode("utf-8")
            )
            # ``set -euo pipefail`` is load-bearing: a failed removal test must abort
            # before the receipt write so the cleanup receipt can never claim a
            # removal that did not verifiably happen, and the exec must fail. The
            # shielded task keeps the removal alive when a timeout cancels this
            # coroutine, so the receipt is still written before cancellation
            # propagates instead of an asyncio interrupt skipping the cleanup.
            cleanup_task = asyncio.ensure_future(
                self.exec_as_agent(
                    environment,
                    command=(
                        "set -euo pipefail; "
                        f"rm -rf {shlex.quote(remote_home)} {shlex.quote(remote_secrets)}; "
                        f"test ! -e {shlex.quote(remote_home)}; "
                        f"test ! -e {shlex.quote(remote_secrets)}; "
                        f"printf %s {cleanup_receipt} > "
                        f"{shlex.quote(agent_dir)}/runtime-cleanup.json"
                    ),
                )
            )
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                await cleanup_task
                raise
        write_pi_trajectory(
            self.logs_dir / "sessions",
            self.logs_dir / "trajectory.json",
            agent_version=self._version or PI_VERSION,
        )


__all__ = [
    "PI_RUNTIME_ENVIRONMENT",
    "RUNTIME_CLEANUP_RECEIPT",
    "RUNTIME_CONFIGURATION_SHA256",
    "RUNTIME_POLICY",
    "TelemetryDisabledPi",
]
