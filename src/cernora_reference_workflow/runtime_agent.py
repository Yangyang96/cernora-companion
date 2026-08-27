"""Pinned Harbor Codex integration with an observable telemetry-off policy."""

from __future__ import annotations

import logging
import shlex
from typing import ClassVar, override

from harbor.agents.installed.base import CliFlag
from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.runtime_policy import (
    PREINSTALLED_CODEX_CHECK_COMMAND,
    RUNTIME_CLEANUP_RECEIPT,
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
    TELEMETRY_CONFIG_TOML,
)


class _PrivateAuthLogFilter(logging.Filter):
    """Prevent Harbor's debug artifact from persisting the operator auth path."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg == "Codex auth: using auth.json from %s":
            record.msg = "Codex auth: using explicit private authority"
            record.args = ()
        return True


class TelemetryDisabledCodex(Codex):  # type: ignore[misc]
    """Harbor 0.16.1 Codex with strict, captured telemetry and plugin settings."""

    CLI_FLAGS: ClassVar[list[CliFlag]] = [
        *Codex.CLI_FLAGS,
        CliFlag(
            "strict_config",
            cli="--strict-config",
            type="bool",
            default=True,
        ),
    ]

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        """Fail closed unless the exact native Runtime is already in the pinned image."""

        await self.exec_as_agent(
            environment,
            command=PREINSTALLED_CODEX_CHECK_COMMAND,
        )

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        remote_home = self._REMOTE_CODEX_HOME.as_posix()
        agent_dir = EnvironmentPaths.agent_dir.as_posix()
        config = shlex.quote(TELEMETRY_CONFIG_TOML)
        policy = shlex.quote(canonical_json_bytes(RUNTIME_POLICY).decode("utf-8"))
        await self.exec_as_agent(
            environment,
            command=(
                f"mkdir -p {shlex.quote(remote_home)} {shlex.quote(agent_dir)}; "
                f"printf %s {config} > {shlex.quote(remote_home)}/config.toml; "
                f"printf %s {config} > {shlex.quote(agent_dir)}/effective-config.toml; "
                f"printf %s {policy} > {shlex.quote(agent_dir)}/runtime-policy.json; "
                f"CODEX_HOME={shlex.quote(remote_home)} codex features list > "
                f"{shlex.quote(agent_dir)}/effective-features.txt; "
                f"grep -Eq '^plugins[[:space:]]+stable[[:space:]]+false$' "
                f"{shlex.quote(agent_dir)}/effective-features.txt; "
                f"grep -Eq '^unified_exec[[:space:]]+stable[[:space:]]+true$' "
                f"{shlex.quote(agent_dir)}/effective-features.txt"
            ),
            env={"CODEX_HOME": remote_home},
        )
        private_auth_filter = _PrivateAuthLogFilter()
        self.logger.addFilter(private_auth_filter)
        try:
            await super().run(instruction, environment, context)
        finally:
            self.logger.removeFilter(private_auth_filter)
            cleanup_receipt = shlex.quote(
                canonical_json_bytes(RUNTIME_CLEANUP_RECEIPT).decode("utf-8")
            )
            await self.exec_as_agent(
                environment,
                command=(
                    f"test ! -e {shlex.quote(remote_home)}; "
                    f"test ! -e {shlex.quote(self._REMOTE_CODEX_SECRETS_DIR.as_posix())}; "
                    f"printf %s {cleanup_receipt} > "
                    f"{shlex.quote(agent_dir)}/runtime-cleanup.json"
                ),
            )


__all__ = [
    "RUNTIME_CONFIGURATION_SHA256",
    "RUNTIME_POLICY",
    "TELEMETRY_CONFIG_TOML",
    "TelemetryDisabledCodex",
]
