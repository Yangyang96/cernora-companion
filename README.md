# Cernora Reference Workflow

This private-by-default companion repository implements the Cernora Priority 3 reference
coding-Agent workflow and the Priority 4 Milestone 1 Companion Repeat Runner. It runs the exact
approved Harbor and Codex versions locally, freezes closed Attempt exports, and performs all
adaptation, evaluation, packing, and rebuild work through the public `cernora==0.1.2` wheel.

The project is not part of Cernora Core. It owns orchestration, export validation, the
`cernora-reference-coding-v1` Profile, and portable reports. It does not provide a generic
Runtime connector or claim network isolation while Codex provider egress is enabled.

## Priority 4 Milestone 1 Repeat Runner

Version `0.2.0` adds a strict, sequential runner for a frozen RunPlan. Verification prints the
canonical plan identity and the exact ordered Trial matrix; starting live work requires that same
identity as an explicit acceptance token:

```sh
uv run experiment verify /absolute/path/to/run-plan.json
uv run experiment run /absolute/path/to/run-plan.json \
  --output /absolute/path/to/new-execution \
  --accept-plan-id <run-plan-id-from-verify>
uv run experiment resume /absolute/path/to/incomplete-execution
uv run experiment rebuild /absolute/path/to/execution.pack \
  --output /absolute/path/to/new-rebuilt-execution
```

The manual M1 exit gate uses the checked-in
`examples/priority4-m1-native-acceptance.json` plan. It freezes two Cases, the
`normal-policy` and `short-timeout-policy` Configurations, three repetitions per cell, 12 planned
Trials, at most 24 Attempts, and a 7,200-second wall budget. Verify and explicitly accept that
exact identity before the authenticated run; request one graceful stop after at least one Trial,
then resume the same Execution to completion.

`run` and `resume` use the repository's single source-tree-only qualified Harbor/Codex connector;
M1 does not expose a generic connector SDK or native batch Runtime. Attempts and checkpoints are
append-only, concurrency is fixed at one, and a crash with an active record but no atomically
published terminal artifact fails closed as ambiguous. Attempt and total wall-time budgets are
hard frozen limits. Token and money controls are explicitly unavailable because the qualified
connector has no structured authoritative source for them.

A completed execution produces a closed sidecar Execution Pack at `<execution>.pack`. `rebuild`
reads only that pack, performs no live work, and writes a new byte-identical verified Execution
tree. M1 reports lifecycle and completeness facts for each Trial; it computes no aggregate quality
rate, configuration winner, or comparative conclusion. See `docs/repeat-runner.md` for the complete
contract and status-code boundary.

## Offline quality gate

```sh
uv sync --frozen --all-groups
uv run python scripts/verify_public_wheel.py
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/verify_release.py
```

The public-wheel verifier requires the public Package Index. Frozen export evaluation is
offline. `.github/workflows/offline.yml` runs the same frozen-evidence gate on Linux with
CPython 3.12 and 3.13; it contains no credentials and never invokes the live tracer. Live
authenticated execution is a separate manual command:

```sh
export http_proxy="http://127.0.0.1:${PROXY_PORT}"
export https_proxy="http://127.0.0.1:${PROXY_PORT}"
export all_proxy="socks5://127.0.0.1:${PROXY_PORT}"
CODEX_AUTH_JSON_PATH=/absolute/path/to/auth.json \
  uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1.json
```

The real lifecycle matrix uses separately identity-bound inputs:

```sh
# Genuine completed behavioral-failure candidate; the task remains authoritative.
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v2.json

# Three-second effective Agent timeout bound into its own ExperimentSpec.
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1-timeout.json

# Real SIGINT sent to the active in-container Codex process.
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1-interruption.json \
  --operator-interrupt
```

Every live command requires the external `CODEX_AUTH_JSON_PATH` and explicit provider proxy
variables. Dedicated `CERNORA_HTTP_PROXY`, `CERNORA_HTTPS_PROXY`, and `CERNORA_ALL_PROXY` values
take precedence; lowercase conventional variables are also accepted. Loopback hosts are mapped to
`host.docker.internal` only for the Agent container, while schemes and ports remain operator-owned.
Proxy URLs containing credentials are rejected, and raw endpoints are not retained in the frozen
Runtime policy or public report. The v2 task is the separately versioned harder task permitted by
the Priority 3 baseline; it never mutates or relabels the successful v1 export.

## Pinned image acquisition and offline task rebuild

Acquire the exact Codex Runtime archive and build its verified Runtime image from the repository
root. The Dockerfile checks the archive and native binary hashes before creating the tag:

```sh
docker build --platform linux/arm64 \
  --tag cernora-reference/codex-runtime:0.148.0 images/codex-runtime
```

Once that exact Runtime image is local, the two task layers rebuild without network or image pulls:

```sh
docker build --network=none --pull=false --platform linux/arm64 \
  --tag cernora-tiny-calculator:priority3 tasks/tiny-calculator-v1/environment
docker build --network=none --pull=false --platform linux/arm64 \
  --tag cernora-tiny-calculator:priority3-v2 tasks/tiny-calculator-v2/environment
```

Compare the resulting local content IDs with `task.toml` and the checked-in ExperimentSpecs before
using the live tracer. The tracer repeats this identity and platform check before reading auth.

After the successful v1 export exists, derive the labeled fail-closed matrix from that exact
frozen source. The output directory must not already exist and remains private under `exports/`:

```sh
uv run python scripts/generate_derived_matrix.py \
  --spec examples/tiny-calculator-v1.json \
  --export exports/<successful-export> \
  --output exports/<successful-export>-derived-matrix
```

The matrix atomically publishes four identity-bound mutations and records that a provably fake
credential was rejected before publication. It never describes those fixtures as live Runtime
failures.

Never place authentication files, attempts, raw Runtime homes, or unpublished exports in the
repository. See `docs/architecture.md`, `docs/compatibility.md`, and
`docs/release-checklist.md` before operating or reviewing the workflow.
