from __future__ import annotations

import runpy
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from cernora_reference_workflow.common import ContractError
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.spec_builder import (
    DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
    TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    build_tiny_calculator_spec,
    build_tiny_calculator_v2_spec,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = runpy.run_path(str(ROOT / "scripts/run_tracer.py"))


def test_harbor_command_binds_timeout_and_disables_native_retry() -> None:
    command_builder = cast(Callable[..., list[str]], SCRIPT["_harbor_command"])
    spec = build_tiny_calculator_spec(ROOT)
    command = command_builder(
        spec,
        "test-job",
        agent_timeout_multiplier=DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
    )
    timeout_index = command.index("--agent-timeout-multiplier")
    retry_index = command.index("-r")
    assert command[timeout_index + 1] == "1.0"
    assert command[retry_index + 1] == "0"
    assert "--force-build" not in command


def test_live_preflight_requires_exact_prebuilt_task_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = build_tiny_calculator_spec(ROOT)
    expected = f"sha256:{spec.container.image.rsplit('@sha256:', 1)[1]}"

    def inspect_image(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            ["docker", "image", "inspect"],
            0,
            stdout=f"{expected} linux/arm64\n",
            stderr="",
        )

    monkeypatch.setattr(SCRIPT["subprocess"], "run", inspect_image)
    verifier = cast(Callable[[ExperimentSpec], None], SCRIPT["_verify_pinned_task_image"])
    verifier(spec)


def test_live_preflight_rejects_task_image_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = build_tiny_calculator_spec(ROOT)

    def inspect_image(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            ["docker", "image", "inspect"],
            0,
            stdout=f"sha256:{'f' * 64} linux/arm64\n",
            stderr="",
        )

    monkeypatch.setattr(SCRIPT["subprocess"], "run", inspect_image)
    verifier = cast(Callable[[ExperimentSpec], None], SCRIPT["_verify_pinned_task_image"])
    with pytest.raises(ContractError, match="does not match ExperimentSpec"):
        verifier(spec)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ((b"/npm/vendor/aarch64-unknown-linux-musl/bin/codex", b"exec", b"-"), True),
        ((b"/usr/bin/node", b"/npm/@openai/codex/bin/codex.js", b"exec"), False),
        ((b"python", b"-c", b"source containing codex exec"), False),
        ((b"codex", b"features", b"list"), False),
    ],
)
def test_operator_interrupt_matches_only_pinned_native_codex_exec_child(
    argv: tuple[bytes, ...],
    expected: bool,
) -> None:
    matcher = cast(Callable[[tuple[bytes, ...]], bool], SCRIPT["_is_codex_exec_argv"])
    assert matcher(argv) is expected


def test_auth_value_scanner_rejects_nonregex_oauth_and_account_leaks(tmp_path: Path) -> None:
    auth = tmp_path / "external-auth.json"
    auth.write_text(
        '{"tokens":{"access_token":"opaque-access-value-123",'
        '"refresh_token":"opaque-refresh-value-456"},"account_id":"acct-local-789"}',
        encoding="utf-8",
    )
    marker_builder = cast(Callable[[Path], tuple[bytes, ...]], SCRIPT["_auth_value_markers"])
    scanner = cast(
        Callable[[Path, Path, tuple[bytes, ...]], None],
        SCRIPT["_assert_auth_absent"],
    )
    markers = marker_builder(auth)
    retained = tmp_path / "retained"
    retained.mkdir()
    (retained / "otherwise-benign.txt").write_text(
        "prefix opaque-refresh-value-456 suffix",
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="authentication value leaked"):
        scanner(retained, auth, markers)


def test_auth_value_scanner_handles_markers_across_stream_chunks(tmp_path: Path) -> None:
    auth = tmp_path / "external-auth.json"
    marker = "opaque-boundary-token-123456"
    auth.write_text(f'{{"access_token":"{marker}"}}', encoding="utf-8")
    marker_builder = cast(Callable[[Path], tuple[bytes, ...]], SCRIPT["_auth_value_markers"])
    scanner = cast(
        Callable[[Path, Path, tuple[bytes, ...]], None],
        SCRIPT["_assert_auth_absent"],
    )
    retained = tmp_path / "retained"
    retained.mkdir()
    (retained / "large.bin").write_bytes(b"x" * (1024 * 1024 - 7) + marker.encode())
    with pytest.raises(ContractError, match="authentication value leaked"):
        scanner(retained, auth, marker_builder(auth))


def test_auth_sanitizer_redacts_exact_path_and_values_before_retention(tmp_path: Path) -> None:
    auth = tmp_path / "external-auth.json"
    marker = "opaque-boundary-token-123456"
    auth.write_text(f'{{"access_token":"{marker}"}}', encoding="utf-8")
    marker_builder = cast(Callable[[Path], tuple[bytes, ...]], SCRIPT["_auth_value_markers"])
    sanitizer = cast(
        Callable[[Path, Path, tuple[bytes, ...]], None],
        SCRIPT["_sanitize_auth_artifacts"],
    )
    scanner = cast(
        Callable[[Path, Path, tuple[bytes, ...]], None],
        SCRIPT["_assert_auth_absent"],
    )
    retained = tmp_path / "retained"
    retained.mkdir()
    (retained / "job.log").write_text(
        f"auth path={auth.resolve()} token={marker}\n",
        encoding="utf-8",
    )
    markers = marker_builder(auth)

    sanitizer(retained, auth.resolve(), markers)

    scanner(retained, auth.resolve(), markers)
    assert (retained / "job.log").read_text(encoding="utf-8") == (
        "auth path=<redacted-external-codex-auth> token=<redacted-external-codex-auth>\n"
    )
    assert (retained / "auth-redaction.json").read_bytes() == (
        b'{"files":[{"path":"job.log","redaction_count":2}],'
        b'"placeholder":"<redacted-external-codex-auth>",'
        b'"schema_version":"cernora.reference.auth-redaction/v1"}'
    )


def test_auth_sanitizer_refuses_retained_auth_file_before_mutation(tmp_path: Path) -> None:
    auth = tmp_path / "external-auth.json"
    marker = "opaque-boundary-token-123456"
    auth.write_text(f'{{"access_token":"{marker}"}}', encoding="utf-8")
    marker_builder = cast(Callable[[Path], tuple[bytes, ...]], SCRIPT["_auth_value_markers"])
    sanitizer = cast(
        Callable[[Path, Path, tuple[bytes, ...]], None],
        SCRIPT["_sanitize_auth_artifacts"],
    )
    retained = tmp_path / "retained"
    retained.mkdir()
    leaked = retained / "auth.json"
    leaked.write_text(marker, encoding="utf-8")

    with pytest.raises(ContractError, match="prohibited auth file"):
        sanitizer(retained, auth.resolve(), marker_builder(auth))

    assert leaked.read_text(encoding="utf-8") == marker
    assert not (retained / "auth-redaction.json").exists()


def test_auth_sanitizer_rejects_exportable_evidence_without_mutation(tmp_path: Path) -> None:
    auth = tmp_path / "external-auth.json"
    marker = "opaque-boundary-token-123456"
    auth.write_text(f'{{"access_token":"{marker}"}}', encoding="utf-8")
    marker_builder = cast(Callable[[Path], tuple[bytes, ...]], SCRIPT["_auth_value_markers"])
    sanitizer = cast(
        Callable[[Path, Path, tuple[bytes, ...]], None],
        SCRIPT["_sanitize_auth_artifacts"],
    )
    retained = tmp_path / "retained"
    exportable = retained / "trial/agent/codex.txt"
    exportable.parent.mkdir(parents=True)
    exportable.write_text(marker, encoding="utf-8")

    with pytest.raises(ContractError, match="exportable or non-log artifact"):
        sanitizer(retained, auth.resolve(), marker_builder(auth))

    assert exportable.read_text(encoding="utf-8") == marker
    assert not (retained / "auth-redaction.json").exists()


def test_auth_sanitizer_is_all_or_nothing_before_log_redaction(tmp_path: Path) -> None:
    auth = tmp_path / "external-auth.json"
    marker = "opaque-boundary-token-123456"
    auth.write_text(f'{{"access_token":"{marker}"}}', encoding="utf-8")
    marker_builder = cast(Callable[[Path], tuple[bytes, ...]], SCRIPT["_auth_value_markers"])
    sanitizer = cast(
        Callable[[Path, Path, tuple[bytes, ...]], None],
        SCRIPT["_sanitize_auth_artifacts"],
    )
    retained = tmp_path / "retained"
    retained.mkdir()
    log = retained / "job.log"
    log.write_text(marker, encoding="utf-8")
    exportable = retained / "trial/verifier/stdout.txt"
    exportable.parent.mkdir(parents=True)
    exportable.write_text(marker, encoding="utf-8")

    with pytest.raises(ContractError, match="exportable or non-log artifact"):
        sanitizer(retained, auth.resolve(), marker_builder(auth))

    assert log.read_text(encoding="utf-8") == marker
    assert exportable.read_text(encoding="utf-8") == marker
    assert not (retained / "auth-redaction.json").exists()


def test_harbor_spawn_oserror_becomes_recordable_infrastructure_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_spawn(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("deterministic spawn failure")

    monkeypatch.setattr(SCRIPT["subprocess"], "Popen", fail_spawn)
    runner = cast(Callable[..., tuple[int, str | None]], SCRIPT["_run_harbor"])
    assert runner(
        ["harbor"],
        env={},
        job=tmp_path / "attempt",
        operator_interrupt=False,
    ) == (127, None)


def test_checked_specs_select_only_their_identity_bound_timeout() -> None:
    selector = cast(
        Callable[[ExperimentSpec], tuple[float, bool, str]], SCRIPT["_accepted_execution_policy"]
    )
    normal = build_tiny_calculator_spec(ROOT)
    timeout = build_tiny_calculator_spec(
        ROOT,
        timeout_seconds=3,
        agent_timeout_multiplier=TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    )
    interruption = build_tiny_calculator_spec(ROOT, operator_interrupt=True)
    harder = build_tiny_calculator_v2_spec(ROOT)
    assert selector(normal) == (
        DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
        False,
        "examples/tiny-calculator-v1.json",
    )
    assert selector(timeout) == (
        TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
        False,
        "examples/tiny-calculator-v1-timeout.json",
    )
    assert selector(interruption) == (
        DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
        True,
        "examples/tiny-calculator-v1-interruption.json",
    )
    assert selector(harder) == (
        DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
        False,
        "examples/tiny-calculator-v2.json",
    )


@pytest.mark.parametrize(
    ("result", "return_code", "expected"),
    [
        ({"exception_info": None}, 0, "completed"),
        ({"exception_info": {"exception_type": "AgentTimeoutError"}}, 1, "timed-out"),
        ({"exception_info": None}, 130, "interrupted"),
    ],
)
def test_requested_state_classifies_only_supported_terminal_results(
    result: dict[str, Any], return_code: int, expected: str
) -> None:
    classifier = cast(Callable[[dict[str, Any], int], str], SCRIPT["_requested_state"])
    assert classifier(result, return_code) == expected


def test_requested_state_rejects_infrastructure_errors() -> None:
    classifier = cast(Callable[[dict[str, Any], int], str], SCRIPT["_requested_state"])
    with pytest.raises(ContractError, match="before a freezable terminal result"):
        classifier({"exception_info": {"exception_type": "RuntimeError"}}, 1)


@pytest.mark.parametrize(
    ("result", "return_code", "expected"),
    [
        (None, 1, "infrastructure-start-failure"),
        (
            {
                "exception_info": {
                    "exception_type": "AgentSetupTimeoutError",
                    "exception_message": "setup timeout",
                },
                "agent_execution": None,
            },
            1,
            "infrastructure-start-failure",
        ),
        (
            {
                "exception_info": {
                    "exception_type": "NonZeroAgentExitCodeError",
                    "exception_message": "provider returned 503 service unavailable",
                },
                "agent_execution": {"started_at": "ignored-operational-value"},
                "agent_result": None,
                "verifier_result": None,
            },
            1,
            "transient-provider-pre-terminal",
        ),
        (
            {
                "exception_info": {
                    "exception_type": "NonZeroAgentExitCodeError",
                    "exception_message": "agent command rejected its input",
                },
                "agent_execution": {"started_at": "ignored-operational-value"},
                "agent_result": None,
                "verifier_result": None,
            },
            1,
            "runtime-pre-terminal-failure",
        ),
        (
            {
                "exception_info": {"exception_type": "AgentTimeoutError"},
                "agent_execution": {"started_at": "ignored-operational-value"},
            },
            1,
            None,
        ),
        ({"exception_info": None}, 0, None),
    ],
)
def test_preterminal_state_has_a_closed_retry_classification(
    result: dict[str, Any] | None,
    return_code: int,
    expected: str | None,
) -> None:
    classifier = cast(
        Callable[[dict[str, Any] | None, int], str | None],
        SCRIPT["_preterminal_state"],
    )
    assert classifier(result, return_code) == expected
