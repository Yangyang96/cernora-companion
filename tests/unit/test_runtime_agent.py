from __future__ import annotations

from cernora_reference_workflow.common import canonical_json_bytes, sha256_bytes
from cernora_reference_workflow.runtime_agent import (
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
    TELEMETRY_CONFIG_TOML,
    TelemetryDisabledCodex,
)
from cernora_reference_workflow.runtime_policy import (
    CODEX_RUNTIME_INSTALLATION,
    PREINSTALLED_CODEX_CHECK_COMMAND,
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


def test_runtime_policy_disables_non_provider_network_and_telemetry_features() -> None:
    assert RUNTIME_POLICY == {
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
