"""Runtime policy values that remain importable without the optional Harbor dependency."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from urllib.parse import SplitResult, urlsplit, urlunsplit

from cernora_reference_workflow.common import ContractError, canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.experiment_spec import StrictContract

PI_VERSION = "0.84.4"
PI_NODE_VERSION = "22.23.2"
PI_NPM_PACKAGE = "@earendil-works/pi-coding-agent"
PI_INSTALL_ROOT = "/opt/pi"
PI_CLI_PATH = "/usr/local/bin/pi"
PI_CLI_ENTRYPOINT = "/opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
PI_NODE_PATH = "/usr/local/bin/node"
PI_CONFIG_DIR = "/tmp/pi-home"
PI_SECRETS_DIR = "/tmp/pi-secrets"
PI_PACKAGE_LOCK_SHA256 = "3831b9425e6e5c74b2ec63dd3255e8435076693aed04e5eb575d12e203a485f1"
PI_PACKAGE_JSON_SHA256 = "e7d9eb868d163fd96c53fffb05799bbfdb0af20b87f1ac8ea6e0559014caa568"
PI_NODE_TARBALL_SHA256 = "013b59cfd2819703a6f4a14ab891fc46fc2a4e3f5bcd92de3fb4929b43e35b30"
PI_NODE_NATIVE_SHA256 = "1a638b0fe2b68da0489276aca95526c5122fc61ba54d6a2d0d00c1c92ab7b876"
PI_CLI_ENTRYPOINT_SHA256 = "840d1e8e689ed9e4937bcb00b9a810e02a8567d9afb10a47097f11ca93ea1521"
PI_RUNTIME_TARGET: Literal["active-pi-process"] = "active-pi-process"
RUNTIME_INTERRUPT_TARGET: Literal["active-pi-process"] = PI_RUNTIME_TARGET

PI_RUNTIME_ENVIRONMENT = {
    "PI_CODING_AGENT_DIR": PI_CONFIG_DIR,
    "PI_OFFLINE": "1",
    "PI_SKIP_VERSION_CHECK": "1",
    "PI_TELEMETRY": "0",
}

PI_RUNTIME_INSTALLATION = {
    "installation_mode": "preinstalled-runtime-base",
    "node_native_sha256": PI_NODE_NATIVE_SHA256,
    "node_platform": "linux-arm64",
    "node_tarball_sha256": PI_NODE_TARBALL_SHA256,
    "node_version": PI_NODE_VERSION,
    "package_json_sha256": PI_PACKAGE_JSON_SHA256,
    "package_lock_sha256": PI_PACKAGE_LOCK_SHA256,
    "pi_cli_entrypoint_sha256": PI_CLI_ENTRYPOINT_SHA256,
    "pi_cli_path": PI_CLI_PATH,
    "pi_install_root": PI_INSTALL_ROOT,
    "pi_npm_package": PI_NPM_PACKAGE,
    "pi_version": PI_VERSION,
    "python_base_image": (
        "python:3.12.13-slim-bookworm@sha256:"
        "4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
    ),
}

PREINSTALLED_PI_CHECK_COMMAND = (
    "set -euo pipefail; "
    f"test \"$(pi --version)\" = '{PI_VERSION}'; "
    f"test \"$(node --version)\" = 'v{PI_NODE_VERSION}'; "
    "printf '%s  %s\\n' "
    f"'{PI_PACKAGE_LOCK_SHA256}' "
    f"'{PI_INSTALL_ROOT}/package-lock.json' "
    f"'{PI_PACKAGE_JSON_SHA256}' "
    f"'{PI_INSTALL_ROOT}/package.json' "
    f"'{PI_CLI_ENTRYPOINT_SHA256}' "
    f"'{PI_CLI_ENTRYPOINT}' "
    f"'{PI_NODE_NATIVE_SHA256}' "
    "'/usr/local/bin/node' | sha256sum --check --strict >/dev/null"
)

RUNTIME_POLICY = {
    "agent_process_isolated_in_container": True,
    "context_file_discovery": "disabled",
    "ephemeral_auth_cleanup_required": True,
    "extensions_discovery": "disabled",
    "install_telemetry_enabled": False,
    "installation_mode": "preinstalled-runtime-base",
    "offline_environment": {
        "PI_OFFLINE": "1",
        "PI_SKIP_VERSION_CHECK": "1",
        "PI_TELEMETRY": "0",
    },
    "prompt_template_discovery": "disabled",
    "provider_proxy": {
        "configuration": "direct-provider-egress",
        "required": False,
        "value_recording": "redacted",
    },
    "session_persistence_required": True,
    "skill_discovery": "disabled",
    "startup_network_operations": "disabled",
    "theme_discovery": "disabled",
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
    """Resolve optional explicit host proxy settings into the Agent container environment.

    The supported direct provider egress does not require a proxy, so no proxy variable is
    mandatory. When an operator does provide one, ``CERNORA_*`` values take precedence and
    lowercase conventional proxy variables are next, mirroring the historical precedence.
    Raw proxy endpoints are never placed in the frozen Runtime policy or public report.
    """

    resolved: dict[str, str] = {}
    for output_name, input_names in _PROXY_INPUTS.items():
        selected = next((environ[name] for name in input_names if environ.get(name)), None)
        if selected is None:
            continue
        resolved[output_name] = _container_proxy_url(selected, variable=input_names[0])
    if resolved:
        for name in _PROXY_INPUTS:
            if name not in resolved:
                raise ContractError(
                    f"provider proxy configuration is all-or-nothing; missing {name}"
                )
        resolved["NO_PROXY"] = "localhost,127.0.0.1"
    return resolved


RUNTIME_CLEANUP_RECEIPT = {
    "pi_config_dir_removed": True,
    "pi_secrets_dir_removed": True,
}

RUNTIME_CONFIGURATION_SHA256 = sha256_bytes(
    canonical_json_bytes(
        {
            "pi_environment_sha256": sha256_bytes(canonical_json_bytes(PI_RUNTIME_ENVIRONMENT)),
            "pi_runtime_installation": PI_RUNTIME_INSTALLATION,
            "policy": RUNTIME_POLICY,
        }
    )
)


class OperatorInterruptReceipt(StrictContract):
    schema_version: Literal["cernora.reference.operator-interrupt/v1"]
    operator_signal: Literal["SIGINT"]
    target: Literal["active-pi-process"]
    verified_signal_count: Literal[1]


__all__ = [
    "PI_CLI_ENTRYPOINT",
    "PI_CLI_PATH",
    "PI_CONFIG_DIR",
    "PI_INSTALL_ROOT",
    "PI_NPM_PACKAGE",
    "PI_PACKAGE_LOCK_SHA256",
    "PI_RUNTIME_ENVIRONMENT",
    "PI_RUNTIME_INSTALLATION",
    "PI_RUNTIME_TARGET",
    "PI_SECRETS_DIR",
    "PI_VERSION",
    "PREINSTALLED_PI_CHECK_COMMAND",
    "RUNTIME_CLEANUP_RECEIPT",
    "RUNTIME_CONFIGURATION_SHA256",
    "RUNTIME_INTERRUPT_TARGET",
    "RUNTIME_POLICY",
    "OperatorInterruptReceipt",
    "resolve_provider_proxy_environment",
]
