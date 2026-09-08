from __future__ import annotations

from pathlib import Path

import pytest

from cernora_reference_workflow.secrets import (
    PROHIBITED_BASENAMES,
    SecretScanError,
    require_secret_free,
    scan_bytes,
    scan_tree,
)


def test_planted_fake_secret_is_detected_without_echoing_value(tmp_path: Path) -> None:
    planted = tmp_path / "diagnostic.txt"
    planted.write_text("Authorization: Bearer fake_" + "A" * 32, encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert [(item.path, item.kind) for item in findings] == [("diagnostic.txt", "bearer-token")]
    with pytest.raises(SecretScanError) as caught:
        require_secret_free(tmp_path)
    assert "fake_" not in str(caught.value)


def test_prohibited_auth_filename_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SecretScanError, match="prohibited-filename"):
        require_secret_free(tmp_path)


@pytest.mark.parametrize(
    ("name", "content", "kind"),
    (
        ("npm-token.txt", "npm_" + "A" * 36, "npm-token"),
        ("pip-token.txt", "pypi-" + "A" * 32, "pypi-token"),
        (
            "git-url.txt",
            "https://operator:" + "credential123@example.invalid/repo",
            "credential-url",
        ),
        (
            "docker-auth.json",
            '{"auths":{"registry.invalid":{"auth":"' + "dXNlcjpwYXNzd29yZA==" + '"}}}',
            "docker-auth",
        ),
    ),
)
def test_package_and_container_credentials_are_rejected(
    tmp_path: Path,
    name: str,
    content: str,
    kind: str,
) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")
    assert [(item.path, item.kind) for item in scan_tree(tmp_path)] == [(name, kind)]


@pytest.mark.parametrize(
    "relative",
    (*sorted(PROHIBITED_BASENAMES), ".docker/config.json"),
)
def test_credential_configuration_paths_are_rejected(tmp_path: Path, relative: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    assert [(item.path, item.kind) for item in scan_tree(tmp_path)] == [
        (relative, "prohibited-filename")
    ]


def test_task_diagnostic_is_not_an_api_key() -> None:
    assert scan_bytes("diagnostic.txt", b"task-checksum-unavailable") == ()


@pytest.mark.parametrize("prefix", (b"", b"Bearer ", b'"api_key":"', b"key="))
@pytest.mark.parametrize("kind", (b"sk-", b"sk-proj-"))
def test_api_key_boundaries_still_reject_credentials(prefix: bytes, kind: bytes) -> None:
    payload = prefix + kind + b"A" * 32
    assert [item.kind for item in scan_bytes("diagnostic.txt", payload)] == ["openai-api-key"]
