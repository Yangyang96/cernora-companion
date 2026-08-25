"""Runtime policy values that remain importable without the optional Harbor dependency."""

from __future__ import annotations

from typing import Literal

from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.experiment_spec import StrictContract

TELEMETRY_CONFIG_TOML = """[analytics]
enabled = false

[feedback]
enabled = false

[otel]
exporter = "none"

[features]
plugins = false
unified_exec = true
"""
CODEX_RUNTIME_INSTALLATION = {
    "codex_native_sha256": "7515d0b61e723374c68d4acdcb8815e378f84d088b0c50638f27d1094bffe536",
    "codex_platform": "aarch64-unknown-linux-musl",
    "codex_platform_tarball_sha256": (
        "feba463f31f8cda589192d5d3339359c5b35bc1366e36909a62d4e488a6822e5"
    ),
    "codex_version": "0.148.0",
    "installation_mode": "preinstalled-runtime-base",
    "rg_sha256": "e36d0eb52e70696bdf1781392722e05a21bb91d3b7b762ef5ec20e5df2ec687b",
}
PREINSTALLED_CODEX_CHECK_COMMAND = (
    "set -euo pipefail; "
    "test \"$(codex --version)\" = 'codex-cli 0.148.0'; "
    "printf '%s  %s\\n' "
    "'7515d0b61e723374c68d4acdcb8815e378f84d088b0c50638f27d1094bffe536' "
    "'/opt/codex/bin/codex' "
    "'e36d0eb52e70696bdf1781392722e05a21bb91d3b7b762ef5ec20e5df2ec687b' "
    "'/opt/codex/codex-path/rg' | sha256sum --check --strict >/dev/null"
)
RUNTIME_POLICY = {
    "analytics_enabled": False,
    "approval_and_agent_sandbox_bypassed_inside_container": True,
    "ephemeral_auth_cleanup_required": True,
    "feedback_enabled": False,
    "installation_mode": "preinstalled-runtime-base",
    "otel_exporter": "none",
    "plugins_enabled": False,
    "provider_proxy_url": "http://host.docker.internal:9981",
    "reasoning_summary": "none",
    "unified_exec_enabled": True,
    "web_search": "disabled",
}
RUNTIME_CLEANUP_RECEIPT = {"codex_home_removed": True, "secrets_dir_removed": True}
RUNTIME_CONFIGURATION_SHA256 = sha256_bytes(
    canonical_json_bytes(
        {
            "codex_config_toml_sha256": sha256_bytes(TELEMETRY_CONFIG_TOML.encode("utf-8")),
            "codex_runtime_installation": CODEX_RUNTIME_INSTALLATION,
            "policy": RUNTIME_POLICY,
            "strict_config": True,
        }
    )
)


class OperatorInterruptReceipt(StrictContract):
    schema_version: Literal["cernora.reference.operator-interrupt/v1"]
    operator_signal: Literal["SIGINT"]
    target: Literal["active-codex-process"]
    verified_signal_count: Literal[1]


__all__ = [
    "CODEX_RUNTIME_INSTALLATION",
    "PREINSTALLED_CODEX_CHECK_COMMAND",
    "RUNTIME_CLEANUP_RECEIPT",
    "RUNTIME_CONFIGURATION_SHA256",
    "RUNTIME_POLICY",
    "TELEMETRY_CONFIG_TOML",
    "OperatorInterruptReceipt",
]
