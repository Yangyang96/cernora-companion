"""Deterministic publication-gate secret scanning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from cernora_reference_workflow.common import ContractError, closed_regular_tree

MAX_SCANNED_FILE_BYTES = 16 * 1024 * 1024
PROHIBITED_BASENAMES = frozenset(
    {
        "auth.json",
        ".env",
        ".dockercfg",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "known_hosts",
        "pip.conf",
        "pip.ini",
    }
)
PATTERNS = (
    ("openai-api-key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(rb"gh[opusr]_[A-Za-z0-9]{20,}")),
    ("gitlab-token", re.compile(rb"glpat-[A-Za-z0-9_-]{20,}")),
    ("npm-token", re.compile(rb"\bnpm_[A-Za-z0-9]{32,}\b")),
    ("pypi-token", re.compile(rb"\bpypi-[A-Za-z0-9_-]{20,}\b")),
    ("aws-access-key", re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}")),
    (
        "aws-secret-key",
        re.compile(rb"(?i)aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{40}"),
    ),
    ("private-key", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    (
        "bearer-token",
        re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~+/-]{20,}"),
    ),
    (
        "basic-auth",
        re.compile(rb"(?i)authorization\s*[:=]\s*basic\s+[A-Za-z0-9+/]{12,}={0,2}"),
    ),
    (
        "credential-url",
        re.compile(rb"(?i)\b(?:git|https?|ssh)://[^\s/:@]{1,128}:[^@\s/]{4,256}@"),
    ),
    (
        "docker-auth",
        re.compile(
            rb'(?i)"(?:auth|identitytoken|registrytoken)"\s*:\s*'
            rb'"[A-Za-z0-9+/_\.-]{12,}={0,2}"'
        ),
    ),
    (
        "npm-auth-assignment",
        re.compile(rb"(?i)(?:_authToken|npmAuthToken)\s*[:=]\s*[\"']?[A-Za-z0-9._-]{16,}"),
    ),
)


@dataclass(frozen=True)
class SecretFinding:
    path: str
    kind: str


class SecretScanError(ContractError):
    def __init__(self, findings: tuple[SecretFinding, ...]) -> None:
        self.findings = findings
        summary = ", ".join(f"{item.path}:{item.kind}" for item in findings)
        super().__init__(f"secret scan rejected publication: {summary}")


def scan_tree(root: Path) -> tuple[SecretFinding, ...]:
    findings: list[SecretFinding] = []
    for relative, path in closed_regular_tree(root).items():
        findings.extend(scan_bytes(relative, path.read_bytes()))
    return tuple(sorted(findings, key=lambda item: (item.path, item.kind)))


def scan_bytes(relative: str, data: bytes) -> tuple[SecretFinding, ...]:
    """Scan one named publication member through the shared credential policy."""

    lower_parts = tuple(part.lower() for part in Path(relative).parts)
    prohibited_docker_config = len(lower_parts) >= 2 and lower_parts[-2:] == (
        ".docker",
        "config.json",
    )
    if lower_parts[-1] in PROHIBITED_BASENAMES or prohibited_docker_config:
        return (SecretFinding(relative, "prohibited-filename"),)
    if len(data) > MAX_SCANNED_FILE_BYTES:
        return (SecretFinding(relative, "scan-size-limit"),)
    return tuple(
        SecretFinding(relative, kind) for kind, pattern in PATTERNS if pattern.search(data)
    )


def require_secret_free(root: Path) -> None:
    findings = scan_tree(root)
    if findings:
        raise SecretScanError(findings)
