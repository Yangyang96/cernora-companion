# Priority 3 native compatibility record

This record separates observed native evidence from deterministic offline acceptance. Relative
paths below name ignored local evidence and are not publication artifacts.

## Fixed environment

| Surface | Observed value |
| --- | --- |
| Host | macOS 26.4 build 25E246, Apple Silicon arm64 |
| Python | CPython 3.13.12 host workflow; CPython 3.12.13 candidate image |
| uv | 0.10.2 |
| Docker | client 29.6.1 darwin/arm64; server 29.6.1 linux/arm64 |
| Docker Compose | 5.3.0 |
| Harbor | 0.16.1 |
| Codex CLI | 0.148.0 |
| Model | gpt-5.6-terra, reasoning effort medium |
| Runtime base | cernora-reference/codex-runtime at sha256:78f59c16c741b9dccdadf1ab2746a9d7fe7a4df8448c848ea50059f22a56d089 |
| Upstream base | python:3.12.13-slim-bookworm at sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 |
| Cernora | public wheel 0.1.2 at sha256:01de19a484172cc8e3940792b90de04683da600320d154fff18b0a717738a2df |

## Installation spike

Native Harbor installation-only attempts were immutable and used no Harbor retry:

| # | Trial identity | Outcome | Boundary learned |
| ---: | --- | --- | --- |
| 1 | a4d39702-1844-486d-a3cc-d1b197b8d5c3 | infrastructure start failure | Docker Compose plugin discovery was absent. |
| 2 | 3b7cd417-417e-489d-97d5-d1ad04f86cb5 | Agent setup timeout at 360 seconds | Debian HTTP index transfer stalled through the Docker proxy. |
| 3 | 81fe5f6f-a01f-47cd-8793-fe336f5cd820 | Agent setup timeout at 360 seconds | HTTPS fixed apt; Node 22 download exceeded the default setup limit. |
| 4 | 6de56276-646a-4728-a71a-b2289faf72ce | completed install-only setup | A 4x setup multiplier completed and reported Codex CLI 0.148.0. |

The compatibility spike initially bound a 1,440-second Agent setup limit and HTTPS Debian source
adjustment. The user-level Compose discovery fix points to the already installed Compose binary;
no Docker data or unrelated container was changed.

The exact `@openai/codex@0.148.0` npm launcher and the exact Linux/arm64 platform package were
inspected from their registry tarballs. A first authenticated tracer preserved two immutable
inconclusive attempts under `attempts/priority3-live-success-20260824-attempt-{1,2}`. Attempt 1
ended in Agent setup after the Node distribution transfer failed. Attempt 2 completed the stock
installation, but Harbor's later independent shell could not resolve the NVM-installed executable
and failed with `codex: command not found`. The tracer retained the eligible 10-second retry link,
did not create a completed export, and reported `runtime-pre-terminal-failure` rather than pass.

The companion fix does not patch Harbor or change the approved versions. A separate runtime base
extracts the exact Apache-2.0 `0.148.0-linux-arm64` platform archive only after checking archive
SHA-256 `feba463f31f8cda589192d5d3339359c5b35bc1366e36909a62d4e488a6822e5`.
It then verifies native Codex SHA-256
`7515d0b61e723374c68d4acdcb8815e378f84d088b0c50638f27d1094bffe536`, bundled ripgrep SHA-256
`e36d0eb52e70696bdf1781392722e05a21bb91d3b7b762ef5ec20e5df2ec687b`, and the exact
`codex-cli 0.148.0` version. A no-network, read-only probe passed. The Runtime adapter now fails
closed unless those preinstalled bytes are present, so live setup performs no package download.
The interruption matcher selects exactly one native `codex exec` process and rejects its own
Python monitor command line before recording SIGINT.

The first authenticated run with that preinstalled Runtime used job
`priority3-live-success-runtime-base-20260824-attempt-1`. Agent setup completed in seconds and an
exact native `codex exec` process was observed in the pinned task image, but direct Docker bridge
egress to `chatgpt.com` timed out. The Codex session recorded WebSocket retries, HTTPS fallback,
and a final network-wait state before Harbor produced `AgentTimeoutError` at the bound 300-second
limit. No completed export was created. A host-only unauthenticated HTTPS probe reached ChatGPT
and received HTTP 403; the same direct container probe timed out; the container probe through
`http://host.docker.internal:9981` reached ChatGPT and received HTTP 403. The endpoint belongs to
the observed local ClashX listener and carries no embedded credentials. The operator subsequently
authorized this exact endpoint for the task. The fixed Runtime image binds `HTTP_PROXY`,
`HTTPS_PROXY` and `ALL_PROXY` to it, records the URL in Runtime policy and therefore changes the
Runtime configuration and every Experiment identity. `NO_PROXY` is limited to localhost. A
no-network, read-only probe of the derived Runtime verified the exact native binary hashes and all
four environment values before live use.

The failed run also proved that Harbor writes the external auth file path into `job.log`. The
pre-publication marker scan stopped the tracer before freeze. A value-invisible diagnostic found
one path occurrence and no token, account, email, or other auth-value marker. The tracer now
atomically replaces exact external-auth path/value bytes only in Harbor's non-exported `job.log`
and `trial.log`, writes a closed `auth-redaction/v1` receipt with path and count but no source value
or digest, rejects any retained `auth.json`, and rescans the result. A marker in candidate, test,
session, trajectory, Runtime or any other exportable/non-log artifact rejects the attempt before
any file is changed. The isolated attempt was sanitized with that implementation and passed the
auth-marker rescan; its failed Runtime evidence remains private and immutable apart from the
recorded non-evidence log redaction.

## Offline task authority observations

After the separately acquired runtime base was present, both task images rebuilt with
`--network=none --pull=false` on linux/arm64. Test Runner execution also used `--network=none`.

| Task | Local image identity | Baseline authority receipt |
| --- | --- | --- |
| tiny-calculator-v1 | sha256:8757db06999b128f3efb3381bf43d92d4c548e7009738ec2ee7ab925fb50b13f | 2 F2P failed, 2 P2P passed, exit 1 |
| tiny-calculator-v2 | sha256:0381f9feca7380640170e0b10a7b927263bed11ba04664290830616d8d598187 | 9 F2P failed, 3 P2P passed, exit 1 |

Direct inspection of the upstream base, runtime base and both task images reports
`PYTHON_VERSION=3.12.13`.
The earlier `3.12.12` label was corrected without changing the official manifest digest. Harbor
now selects each exact local task image content ID from `task.toml` with `force_build=false`;
the live tracer verifies the image ID and linux/arm64 platform before reading auth and again from
the running container. This avoids treating a cold-cache Docker rebuild ID as reproducible.

## Local Linux offline acceptance

An auxiliary Linux/arm64 acceptance image was assembled from 101 uv.lock-selected wheels whose
downloaded bytes were checked against the lockfile hashes, plus the exact Hatchling 1.27.0 build
backend. Dependency and companion installation ran under `docker build --network=none`.
The ephemeral local acceptance image then ran the private publication gate with
`--network=none`, a read-only root filesystem and only an ephemeral `/tmp` writable. The result
was 146 tests, Ruff, mypy, isolated wheel/sdist build,
license coverage and secret scans all passing. This is private supporting evidence; the hosted
GitHub Actions matrix remains unexecuted because no commit or push is authorized.

## Experiment identities and live matrix

| Required real case | Experiment identity | Attempt identity | Status |
| --- | --- | --- | --- |
| successful repair | 2fa7df8a1dbe2407e6ca716a4a8f5f5e69f7a3e5c58ca528bc5046dcbc006e4c | 51b2fa0b77220485ae8e3806a840ec31ee7dbc234360e59f153dd621ba5cf875 | completed/pass; Harbor reward 1.0 |
| completed behavioral failure | fc0afafe3230c9170ca8895c76fc570462e4cadd79daf5155d61838ffc349ab5 | cf52a251836176c7c4b7d6c250e5dda948b116574731d103994053b5d6033fb5 | behavioral-failure/fail despite Harbor reward 1.0 |
| timeout | ff11231521b7833ccead54f4fa4820824b7be4c4109231a162aa6793f50cd2d0 | fc5f702ac395731174acd8b809dbe33ec996a55aed48964c7825dd71e584269f | timed-out/inconclusive; no retry |
| operator interruption | 4e8521222b222cbbdbe802b1ff0a67f27de779dd7a9a5795be7fa01737683905 | 5d216f7c75c5daeeab38c9d5c3f9bed4d22a66b21292b2c844d1452c936d45f2 | interrupted/inconclusive; one verified SIGINT; no retry |

The telemetry-off configuration is accepted by Codex strict-config parsing and explicitly sets
analytics false, feedback false, OTEL exporter none, plugins false, and unified execution true.
Web search is disabled. The earlier direct-egress diagnostic nevertheless observed Codex requests to the
provider `backend-api/ps/mcp` endpoint while the effective `plugins` feature reported false; this
is retained as provider-egress evidence and is not represented as proof that remote plugin-related
traffic was absent. The successful Runtime boundary recorded provider egress as observed and zero
matches for the prohibited telemetry patterns, with effective analytics and feedback disabled and
the OTEL exporter set to none. All four live jobs passed the external-auth marker scan, removed the
ephemeral Runtime and secrets homes, and verified the trial container absent. The successful export
also produced deterministic derived matrix
`2521a4f58717b2fa648fe93ea4cc05662448a535e0178b5fe8cd4280eb16512b` with the complete four
mutation recipes; the planted fake credential was rejected before publication. The final scanner
also covers Docker auth payloads/config paths, Git credential URLs/files, npm tokens/config, pip
tokens/config, registry Basic auth and existing provider/cloud/private-key patterns. Its expanded
scan passed all four raw attempts, exports and reports. Native Supported Preview acceptance is
observed locally. Hosted CI and publication remain pending explicit owner authorization.
