from __future__ import annotations

import io
import logging

import pytest

import cernora_reference_workflow.runtime_agent as runtime_agent_module
from cernora_reference_workflow.common import ContractError, canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.runtime_agent import (
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
    TELEMETRY_CONFIG_TOML,
    TelemetryDisabledCodex,
)
from cernora_reference_workflow.runtime_policy import (
    CODEX_RUNTIME_INSTALLATION,
    PREINSTALLED_CODEX_CHECK_COMMAND,
    resolve_provider_proxy_configuration,
    resolve_provider_proxy_environment,
)


def test_runtime_configuration_identity_is_frozen_and_strict_config_is_enabled() -> None:
    expected = sha256_bytes(
        canonical_json_bytes(
            {
                "codex_config_toml_sha256": sha256_bytes(TELEMETRY_CONFIG_TOML.encode("utf-8")),
                "codex_runtime_installation": CODEX_RUNTIME_INSTALLATION,
                "policy": RUNTIME_POLICY,
                "strict_config": True,
            }
        )
    )
    assert expected == RUNTIME_CONFIGURATION_SHA256
    strict = [flag for flag in TelemetryDisabledCodex.CLI_FLAGS if flag.kwarg == "strict_config"]
    assert len(strict) == 1
    assert strict[0].default is True


def test_explicit_auth_log_is_redacted_before_harbor_persists_it() -> None:
    private_path = "/private/auth-location/auth.json"
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    logger = logging.getLogger("cernora-test-auth-redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    private_filter = runtime_agent_module._PrivateAuthLogFilter()
    logger.addFilter(private_filter)
    try:
        logger.debug("Codex auth: using auth.json from %s", private_path)
        logger.debug("unrelated runtime observation")
    finally:
        logger.removeFilter(private_filter)
        handler.close()
        logger.handlers = []

    persisted = output.getvalue()
    assert private_path not in persisted
    assert "auth.json from" not in persisted
    assert "Codex auth: using explicit private authority" in persisted
    assert "unrelated runtime observation" in persisted


def test_runtime_policy_disables_non_provider_network_and_telemetry_features() -> None:
    assert RUNTIME_POLICY == {
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


def test_runtime_installation_is_preinstalled_exact_and_network_free() -> None:
    assert CODEX_RUNTIME_INSTALLATION == {
        "codex_native_sha256": ("7515d0b61e723374c68d4acdcb8815e378f84d088b0c50638f27d1094bffe536"),
        "codex_platform": "aarch64-unknown-linux-musl",
        "codex_platform_tarball_sha256": (
            "feba463f31f8cda589192d5d3339359c5b35bc1366e36909a62d4e488a6822e5"
        ),
        "codex_version": "0.148.0",
        "installation_mode": "preinstalled-runtime-base",
        "rg_sha256": "e36d0eb52e70696bdf1781392722e05a21bb91d3b7b762ef5ec20e5df2ec687b",
    }
    assert "codex --version" in PREINSTALLED_CODEX_CHECK_COMMAND
    assert "sha256sum --check --strict" in PREINSTALLED_CODEX_CHECK_COMMAND
    assert all(
        token not in PREINSTALLED_CODEX_CHECK_COMMAND
        for token in ("curl", "npm", "nvm", "apt-get", "http://", "https://")
    )


def test_provider_proxy_is_external_and_loopback_is_mapped_into_docker() -> None:
    resolved = resolve_provider_proxy_environment(
        {
            "http_proxy": "http://127.0.0.1:18080",
            "https_proxy": "http://localhost:18080",
            "all_proxy": "socks5://127.0.0.1:11080",
        }
    )
    assert resolved == {
        "HTTP_PROXY": "http://host.docker.internal:18080",
        "HTTPS_PROXY": "http://host.docker.internal:18080",
        "ALL_PROXY": "socks5://host.docker.internal:11080",
        "NO_PROXY": "localhost,127.0.0.1",
    }


def test_proxy_private_scan_inputs_exclude_unrelated_ambient_values() -> None:
    configuration = resolve_provider_proxy_configuration(
        {
            "http_proxy": "http://127.0.0.1:7890",
            "https_proxy": "http://127.0.0.1:7890",
            "all_proxy": "socks5://127.0.0.1:7890",
            "NO_COLOR": "1",
            "SHLVL": "2",
            "TERM": "dumb",
        }
    )

    assert configuration.source_endpoints == (
        "http://127.0.0.1:7890",
        "socks5://127.0.0.1:7890",
    )
    assert not {"1", "2", "dumb"}.intersection(configuration.source_endpoints)


@pytest.mark.parametrize(
    "value",
    (
        "http://" + "user:" + "secret@" + "proxy.invalid:8080",
        "ftp://proxy.invalid:21",
        "http://proxy.invalid",
        "http://proxy.invalid:8080/path",
    ),
)
def test_provider_proxy_rejects_unsafe_or_ambiguous_urls(value: str) -> None:
    with pytest.raises(ContractError, match="proxy URL"):
        resolve_provider_proxy_environment(
            {
                "CERNORA_HTTP_PROXY": value,
                "CERNORA_HTTPS_PROXY": value,
                "CERNORA_ALL_PROXY": value,
            }
        )
