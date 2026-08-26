"""Runtime policy values that remain importable without the optional Harbor dependency."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from urllib.parse import SplitResult, urlsplit, urlunsplit

from cernora_reference_workflow.common import ContractError, canonical_json_bytes, sha256_bytes
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
    "provider_proxy": {
        "configuration": "operator-environment",
        "required": True,
        "value_recording": "redacted",
    },
    "reasoning_summary": "none",
    "unified_exec_enabled": True,
    "web_search": "disabled",
}

_PROXY_INPUTS = {
    "HTTP_PROXY": ("CERNORA_HTTP_PROXY", "http_proxy", "HTTP_PROXY"),
    "HTTPS_PROXY": ("CERNORA_HTTPS_PROXY", "https_proxy", "HTTPS_PROXY"),
    "ALL_PROXY": ("CERNORA_ALL_PROXY", "all_proxy", "ALL_PROXY"),
}
_SUPPORTED_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})
_LOOPBACK_PROXY_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _container_proxy_url(value: str, *, variable: str) -> str:
    """Validate one credential-free proxy URL and map host loopback into Docker."""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ContractError(f"{variable} is not a valid proxy URL") from exc
    if (
        parsed.scheme not in _SUPPORTED_PROXY_SCHEMES
        or parsed.hostname is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ContractError(
            f"{variable} must be a credential-free http(s) or socks5 proxy URL with a port"
        )
    host = parsed.hostname.lower()
    if host in _LOOPBACK_PROXY_HOSTS:
        host = "host.docker.internal"
    elif ":" in host:
        host = f"[{host}]"
    return urlunsplit(SplitResult(parsed.scheme, f"{host}:{port}", "", "", ""))


def resolve_provider_proxy_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """Resolve explicit host proxy settings into the Agent container environment.

    Dedicated ``CERNORA_*`` values take precedence. Lowercase conventional proxy variables are
    next so an operator's explicit shell exports override unrelated inherited uppercase values.
    Raw proxy endpoints are never placed in the frozen Runtime policy or public report.
    """

    resolved: dict[str, str] = {}
    for output_name, input_names in _PROXY_INPUTS.items():
        selected = next((environ[name] for name in input_names if environ.get(name)), None)
        if selected is None:
            raise ContractError(f"live execution requires one of: {', '.join(input_names)}")
        resolved[output_name] = _container_proxy_url(selected, variable=input_names[0])
    resolved["NO_PROXY"] = "localhost,127.0.0.1"
    return resolved


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
    "resolve_provider_proxy_environment",
]
