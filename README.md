# Cernora Reference Workflow

See [P4 result preservation and first cleanup](docs/p4-first-cleanup.md) for the completed
72-Trial result, retired research tools, and historical reproduction boundary.

This private-by-default companion repository implements the Cernora Priority 3 reference
coding-Agent workflow and the Priority 4 Companion Repeat Runner. Version `0.2.0` runs the exact
approved Harbor and pi versions locally, freezes closed Attempt exports, and performs its
adaptation, evaluation, packing, and rebuild work through the public `cernora==0.1.2` wheel. The
additive `0.2.1` Milestone 2 local release candidate consumes those frozen Packs with the matching
Cernora Core `0.1.3` local release candidate. Additive `0.3.0` assembles authority-bound controlled
comparisons with the accepted Core `0.1.4` local release candidate.

Companion `0.4.0` replaces the parallel milestone orchestration paths with one durable Controlled
Study application. Its supported common-root interface is exactly `prepare`, idempotent `advance`,
and offline-only `rebuild`; it reuses the Repeat Runner, Execution Pack, and Core Batch/Comparison
contracts rather than introducing another execution engine.

The project is not part of Cernora Core. It owns orchestration, export validation, the
`cernora-reference-coding-v1` Profile, and portable reports. It does not provide a generic
Runtime connector or claim network isolation while pi provider egress is enabled.

Companion `0.4.0` and Cernora Core `0.1.4` have not been publicly released. Their M3/M4 artifacts
and acceptance evidence remain local.

## Priority 4 Controlled Study

The supported CLI is one `study` command group with three operations:

```sh
uv run experiment study prepare /absolute/path/to/study-intent.json \
  --output /absolute/path/to/new-study
uv run experiment study advance /absolute/path/to/study \
  --directive /absolute/path/to/advance-directive.json
uv run experiment study rebuild /absolute/path/to/terminal-artifact \
  --output /absolute/path/to/new-offline-rebuild
```

`prepare` freezes implementation digests, Candidate authority, disjoint development/regression/
held-out claims, the complete adjacent AB/BA schedule, budgets, and analysis policy without doing
external work. Each `advance` directive is bound to the prior state identity, appends a durable
claim before any Runtime invocation, and can claim at most one external Attempt. Repeating it
returns or recovers the same transition; an unresolved active Attempt pauses as ambiguous instead
of being retried.

A pause or termination publishes a diagnostic-only pack with no Batch or Comparison authority.
Only a complete evaluated matrix publishes an Evidence Pack containing its ledger prefix,
Execution Pack, strict Core Batch, and held-out-primary Comparison. `study rebuild` uses no Runtime
or credentials: it rederives the Core packages and reproduces the exact closed artifact bytes.
See `docs/controlled-study.md` for the state, custody, and authority contracts.
Before any authenticated study, complete `docs/live-controlled-study-dossier.md`; it freezes the
human review record and live authorization boundary but never replaces the canonical Study
authorities or grants permission to execute.

The checked-in `preparations/next-priority4-controlled-study` bundle is the machine-verifiable
starting point for that review. It binds the current offline Core and Companion wheel candidates
and carries an explicitly non-binding recommendation for a confirmatory held-out analysis and
9 × 2 × 3 bounds. The study mode, scientific question, fresh Candidate, independent reviewer,
fresh held-out custodian/commitment, remaining implementation lock, and administrative fields all
remain caller-owned and pending. It contains no `StudyIntent`, reveal, acceptance, execution
nonce, RunPlan, ComparisonPlan, or execution directive. Use
`scripts/create_study_preparation_bundle.py verify` to strictly reload it against the exact wheel
bytes; this maintenance script is not a fourth `experiment study` operation.

The standalone `verify`, `run`, `resume`, `rebuild`, `summarize`, and `compare` commands below are
retained only to read or reproduce historical M1–M3 artifacts. They are not the supported path for
new Priority 4 studies and do not define a second orchestration architecture.

## Priority 4 Milestone 3 Controlled Comparison

Companion `0.3.0` adds one offline assembly command for a strict Core Batch Summary package and
predeclared V2 authorities:

```sh
uv run experiment compare /absolute/path/to/batch-summary \
  --run-plan /absolute/path/to/controlled-run-plan.json \
  --plan /absolute/path/to/comparison-plan.json \
  --output /absolute/path/to/new-comparison
```

Controlled ExperimentSpec/RunPlan V2 uses Core's neutral Experiment authority as the Experiment ID
before execution. Companion rederives every projection and Treatment endpoint from typed canonical
sources, checks the complete Trial matrix and any strict Evaluation receipts, then asks Core to
publish and reload the authoritative Comparison package. Legacy M1/M2 Experiment IDs remain frozen
and are rejected instead of being rewritten after execution.

Core owns pairing, statistics, hard Guardrails, failure migration, and conclusions. Companion does
not choose a winner, rank Configurations, promote an artifact, or convert a regressed result into a
command failure. See `docs/controlled-comparison.md` for the complete identity and exit-status
boundary.

## Priority 4 Milestone 2 Batch Summary

Companion `0.2.1` adds one offline consumer command for a completed, closed M1 Execution Pack:

```sh
uv run experiment summarize /absolute/path/to/execution.pack \
  --output /absolute/path/to/new-summary
```

The command strictly verifies and reloads the Pack, normalizes its frozen plan, Trials, Attempt
lineage, selected Evaluation Packages, lifecycle receipts, and available resource receipts into a
Cernora Core `0.1.3` `BatchInput`, then asks Core to atomically publish and strictly reload the
validity-first `BatchSummary`. The output directory must not already exist. Repeating the command
from the same Pack into new directories produces byte-identical authoritative summary bytes.

The four exhaustive Trial outcomes are `pass`, `behavioral_fail`, `evaluation_invalid`, and
`infrastructure_unavailable`. An Evaluation Package that exists but is invalid remains
`evaluation_invalid`; it is not relabeled as an infrastructure failure. Retry Attempts remain one
Trial lineage and are diagnostics, not independent Trials.

M2 summarizes one Execution only. It provides no comparison, delta, interval, pass-at-k,
`pass^k`, ranking, promotion, or winner. See `docs/batch-summary.md` for the complete
normalization and exit-status boundary.

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

`run` and `resume` use the repository's single source-tree-only qualified Harbor/pi connector;
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

The M3 lock resolves Core from the stable sibling wheelhouse
`../cernora/dist/cernora-0.1.4-py3-none-any.whl`. Build the accepted Core `0.1.4` candidate into
that ignored directory first and verify its SHA-256 is
`4ef10a5eb2f9961943883576ab81bc97ce32d2f3f8a88cb9679d5c51c81e368d`. The wheel is a local
release artifact; do not commit or upload it.

```sh
uv sync --frozen --all-groups --offline
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/verify_release.py
uv run python scripts/verify_comparison_wheels.py \
  --core-wheel ../cernora/dist/cernora-0.1.4-py3-none-any.whl \
  --companion-wheel /absolute/path/to/cernora_reference_workflow-0.4.0-py3-none-any.whl
```

The public-wheel verifier and current checked-in workflow remain historical Priority 3/M1
`cernora==0.1.2` gates at tag `v0.2.0`; they are not M3 release signals. The historical M2 Batch
wheel verifier remains available for its accepted 0.1.3/0.2.1 pair. M3 installs the separately
built Core `0.1.4` and Companion `0.4.0` wheels without treating either as public. Local offline
gates contain no credentials and never invoke the live tracer. Live authenticated execution is a
separate manual command:

```sh
PI_AUTH_JSON_PATH=/absolute/path/to/pi-auth.json \
  uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1.json
```

The external auth file uses pi's `auth.json` shape; for the pinned direct provider it contains
exactly the provider-keyed entry, for example
`{"deepseek": {"type": "api_key", "key": "<DeepSeek API key>"}}`.

The real lifecycle matrix uses separately identity-bound inputs:

```sh
# Genuine completed behavioral-failure candidate; the task remains authoritative.
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v2.json

# Three-second effective Agent timeout bound into its own ExperimentSpec.
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1-timeout.json

# Real SIGINT sent to the active in-container pi agent process.
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1-interruption.json \
  --operator-interrupt
```

Every live command requires the external `PI_AUTH_JSON_PATH`; the pinned provider (DeepSeek,
direct egress at api.deepseek.com) needs no proxy. Optional explicit provider proxy variables
remain honored for operators routing through international providers: dedicated `CERNORA_HTTP_PROXY`,
`CERNORA_HTTPS_PROXY`, and `CERNORA_ALL_PROXY` values take precedence, lowercase conventional
variables are also accepted, and the three entries must be provided all-or-nothing. Loopback hosts
are mapped to `host.docker.internal` only for the Agent container, while schemes and ports remain
operator-owned. Proxy URLs containing credentials are rejected, and raw endpoints are not retained
in the frozen Runtime policy or public report. The v2 task is the separately versioned harder task permitted by
the Priority 3 baseline; it never mutates or relabels the successful v1 export.

## Pinned image acquisition and offline task rebuild

Acquire the exact pi Runtime context and build its verified Runtime image from the repository
root. The build is fully offline: the Dockerfile verifies the official Node.js tarball, the
vendored dependency tree, and the pi CLI bytes before creating the tag. Prepare the local build
context first (node tarball plus `npm ci` tree) exactly as documented in `images/pi-runtime/README.md`:

```sh
docker build --platform linux/arm64 \
  --tag cernora-reference/pi-runtime:0.84.4 images/pi-runtime
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
