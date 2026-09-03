from __future__ import annotations

import pytest

from cernora_reference_workflow.common import (
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
)
from cernora_reference_workflow.pi_trajectory import convert_pi_session
from cernora_reference_workflow.runtime_agent import TelemetryDisabledPi
from cernora_reference_workflow.runtime_policy import (
    PI_CLI_ENTRYPOINT,
    PI_NODE_PATH,
    PI_RUNTIME_INSTALLATION,
    PI_VERSION,
    PREINSTALLED_PI_CHECK_COMMAND,
    RUNTIME_CONFIGURATION_SHA256,
    RUNTIME_POLICY,
    resolve_provider_proxy_environment,
)


def test_runtime_configuration_identity_is_frozen() -> None:
    expected = sha256_bytes(
        canonical_json_bytes(
            {
                "pi_environment_sha256": sha256_bytes(
                    canonical_json_bytes(
                        {
                            "PI_CODING_AGENT_DIR": "/tmp/pi-home",
                            "PI_OFFLINE": "1",
                            "PI_SKIP_VERSION_CHECK": "1",
                            "PI_TELEMETRY": "0",
                        }
                    )
                ),
                "pi_runtime_installation": PI_RUNTIME_INSTALLATION,
                "policy": RUNTIME_POLICY,
            }
        )
    )
    assert expected == RUNTIME_CONFIGURATION_SHA256


def test_runtime_policy_offline_direct_and_discovery_disabled() -> None:
    assert RUNTIME_POLICY == {
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


def test_runtime_installation_is_preinstalled_exact_and_network_free() -> None:
    assert PI_RUNTIME_INSTALLATION == {
        "installation_mode": "preinstalled-runtime-base",
        "node_native_sha256": ("1a638b0fe2b68da0489276aca95526c5122fc61ba54d6a2d0d00c1c92ab7b876"),
        "node_platform": "linux-arm64",
        "node_tarball_sha256": ("013b59cfd2819703a6f4a14ab891fc46fc2a4e3f5bcd92de3fb4929b43e35b30"),
        "node_version": "22.23.2",
        "package_json_sha256": ("e7d9eb868d163fd96c53fffb05799bbfdb0af20b87f1ac8ea6e0559014caa568"),
        "package_lock_sha256": ("3831b9425e6e5c74b2ec63dd3255e8435076693aed04e5eb575d12e203a485f1"),
        "pi_cli_entrypoint_sha256": (
            "840d1e8e689ed9e4937bcb00b9a810e02a8567d9afb10a47097f11ca93ea1521"
        ),
        "pi_cli_path": "/usr/local/bin/pi",
        "pi_install_root": "/opt/pi",
        "pi_npm_package": "@earendil-works/pi-coding-agent",
        "pi_version": PI_VERSION,
        "python_base_image": (
            "python:3.12.13-slim-bookworm@sha256:"
            "4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
        ),
    }
    assert "pi --version" in PREINSTALLED_PI_CHECK_COMMAND
    assert "node --version" in PREINSTALLED_PI_CHECK_COMMAND
    assert "sha256sum --check --strict" in PREINSTALLED_PI_CHECK_COMMAND
    assert PI_CLI_ENTRYPOINT in PREINSTALLED_PI_CHECK_COMMAND
    assert PI_NODE_PATH in PREINSTALLED_PI_CHECK_COMMAND
    assert all(
        token not in PREINSTALLED_PI_CHECK_COMMAND
        for token in ("curl", "npm", "nvm", "apt-get", "http://", "https://")
    )


def test_pi_agent_class_pins_preinstalled_runtime_identity() -> None:
    agent = TelemetryDisabledPi.__new__(TelemetryDisabledPi)
    assert TelemetryDisabledPi.name() == "pi"
    assert agent.get_version_command() == "pi --version"


def test_direct_provider_execution_requires_no_proxy() -> None:
    assert resolve_provider_proxy_environment({}) == {}


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


def test_provider_proxy_is_all_or_nothing() -> None:
    with pytest.raises(ContractError, match="all-or-nothing"):
        resolve_provider_proxy_environment({"http_proxy": "http://127.0.0.1:18080"})


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


def test_pi_trajectory_conversion_maps_usage_and_tool_results(tmp_path):
    session = tmp_path / "session.jsonl"
    session.write_text(
        "\n".join(
            (
                '{"type":"session","version":3,"id":"test-session","cwd":"/workspace"}',
                '{"type":"message","id":"m1","parentId":null,"message":{'
                '"role":"user","content":"add the numbers","timestamp":1000}}',
                '{"type":"message","id":"m2","parentId":"m1","message":{'
                '"role":"assistant","content":[{"type":"text","text":"editing"},'
                '{"type":"toolCall","id":"call-1","name":"edit","arguments":{"path":"src/calc.py"}}],'
                '"api":"openai-completions","provider":"deepseek","model":"deepseek-v4-flash",'
                '"usage":{"input":10,"output":20,"cacheRead":5,"cacheWrite":0,"totalTokens":35,'
                '"cost":{"input":0.1,"output":0.2,"cacheRead":0.05,"cacheWrite":0,"total":0.35}},'
                '"stopReason":"stop","timestamp":2000}}',
                '{"type":"message","id":"m3","parentId":"m2","message":{'
                '"role":"toolResult","toolCallId":"call-1","toolName":"edit",'
                '"content":[{"type":"text","text":"done"}],"isError":false,"timestamp":3000}}',
                '{"type":"message","id":"m4","parentId":"m3","message":{'
                '"role":"assistant","content":[{"type":"text","text":"added"}],'
                '"api":"openai-completions","provider":"deepseek","model":"deepseek-v4-flash",'
                '"usage":{"input":30,"output":40,"cacheRead":10,"cacheWrite":0,"totalTokens":80,'
                '"cost":{"input":0.3,"output":0.4,"cacheRead":0.1,"cacheWrite":0,"total":0.8}},'
                '"stopReason":"stop","timestamp":4000}}',
            )
        ),
        encoding="utf-8",
    )
    trajectory = convert_pi_session(session, agent_version=PI_VERSION)
    assert trajectory.schema_version == "ATIF-v1.7"
    assert trajectory.session_id == "test-session"
    assert trajectory.agent.name == "pi"
    assert trajectory.agent.model_name == "deepseek-v4-flash"
    assert [step.source for step in trajectory.steps] == ["user", "agent", "agent"]
    tool_calls = trajectory.steps[1].tool_calls
    assert tool_calls is not None and tool_calls[0].function_name == "edit"
    observation = trajectory.steps[1].observation
    assert observation is not None
    assert observation.results and observation.results[0].source_call_id == "call-1"
    assert trajectory.steps[1].metrics is not None
    assert trajectory.steps[1].metrics.completion_tokens == 20
    metrics = trajectory.final_metrics
    assert metrics is not None
    assert metrics.total_prompt_tokens == 40
    assert metrics.total_completion_tokens == 60
    assert metrics.total_cached_tokens == 15
    assert metrics.total_cost_usd == pytest.approx(1.15)
    assert metrics.total_steps == 3
