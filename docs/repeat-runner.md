# Companion Repeat Runner and Batch Summary (Priority 4 Milestones 1–2)

The Companion Repeat Runner is the frozen `cernora-reference-workflow==0.2.0` Milestone 1
orchestration boundary. It is not Cernora Core, a generic Runtime connector, or a native batch
service. Its only live adapter is the source-tree-only qualified Harbor `0.16.1` / Codex `0.148.0`
connector already used by this repository. Milestone 2 is an additive offline consumer; it does
not reinterpret or rewrite the M1 contracts described below.

## Frozen RunPlan and preflight

A canonical `cernora.reference.run-plan/v1` embeds every exact ExperimentSpec, ordered Case,
Configuration, matrix cell, repetition count, connector identity, retry ceiling, and budget. Its
content-derived `run_plan_id` changes when any behavior-affecting input changes. Trial slots expand
in cell order and then repetition order; each slot and each execution-bound Trial has a distinct
content identity. Missing referenced Cases, Configurations, or ExperimentSpecs, duplicate cells,
unknown fields, non-canonical JSON, and identity mismatches fail closed.

Preflight is read-only and performs no live work:

```sh
uv run experiment verify /absolute/path/to/run-plan.json
```

The JSON output includes the accepted connector, Cases, Configurations, cells, ordered slots,
planned Trial count, worst-case Attempt count, and fixed budgets. Review those bytes before using
the printed identity as the mandatory acceptance token:

```sh
uv run experiment run /absolute/path/to/run-plan.json \
  --output /absolute/path/to/new-execution \
  --accept-plan-id <exact-run-plan-id>
```

Both the execution directory and its `<execution>.pack` sidecar must be new. Live execution also
requires the external `CODEX_AUTH_JSON_PATH`, explicit credential-free provider proxy variables,
and the same qualified macOS Apple Silicon environment as the Priority 3 tracer. Proxy endpoints
are operational inputs and are redacted from portable evidence.

## Sequential execution, resume, and budgets

Concurrency is fixed at one. The runner freezes an active Attempt record before invoking the live
connector. The connector must atomically publish exactly one strict Priority 3 completed-export or
preterminal artifact at the assigned destination. Records, Attempt artifacts, Trial results,
Trial manifests, and hash-chained checkpoints are append-only.

A retry is permitted only when the preceding strict TerminalRecord is retry-eligible and the
embedded ExperimentSpec still allows it. The global maximum Attempt count and total wall time are
hard limits checked between terminal Attempts and Trial boundaries. Reaching either publishes a
terminal `budget-exhausted` checkpoint; that incomplete execution cannot later be resumed. Token
and monetary budgets are marked `unavailable/no-structured-authoritative-source`, never guessed.

Resume is explicit:

```sh
uv run experiment resume /absolute/path/to/incomplete-execution
```

Verified terminal artifacts, Trial results, Trial manifests, and completion outputs may be
adopted after a crash without rerunning live work. An active record without a verifiable terminal
artifact is ambiguous and blocks resume. Operator stop is recognized only between Trials. A
completed checkpoint missing only its diagnostic, manifest, or sidecar pack is finalized from
verified immutable inputs.

After a native Agent process has terminalized, an export rejected by the shared secret scanner is
retained only as a non-retryable `runtime-pre-terminal-failure`; the unsafe export is never
published. The same closed classification applies to the observed negative Harbor duration
receipt. Missing, malformed, mismatched, or otherwise unknown receipts still fail closed and are
not downgraded to lifecycle evidence.

## Execution Pack and offline rebuild

Completion creates `<execution>.pack`, a closed portable tree whose manifest indexes every file by
relative path, byte length, and SHA-256 digest. The execution directory does not contain its own
pack. Rebuild accepts only a verified completed pack and writes a new Execution directory:

```sh
uv run experiment rebuild /absolute/path/to/execution.pack \
  --output /absolute/path/to/new-rebuilt-execution
```

Rebuild is offline-only: no credentials, network, Runtime, Docker, Git, shell, tests, or external
processes. It regenerates derived completion diagnostics and the manifest, strictly reloads the
resulting Execution tree, and requires every Execution byte to match the tree carried by the source
pack.

M1 diagnostics contain lifecycle and completeness facts only. There is no aggregate score,
quality rate, confidence claim, configuration ranking, winner selection, or comparative quality
conclusion.

## Milestone 2 strict batch normalization

Companion `0.2.1` and Cernora Core `0.1.3` are matching local release candidates; neither version
has been publicly released. Companion adds only this offline command:

```sh
uv run experiment summarize /absolute/path/to/execution.pack \
  --output /absolute/path/to/new-summary
```

The input must be a verified, completed M1 Pack and the output directory must be new. Companion
strictly reloads the Pack, projects its frozen RunPlan and ordered Trials, preserves each Trial's
Attempt lineage, binds selected Evaluation Packages or lifecycle receipts, and copies only
authoritative available resource receipts. It verifies the Pack again after reading the projected
artifacts. Missing, conflicting, malformed, identity-mismatched, or concurrently changed inputs
fail closed.

Companion materializes a strict Core `BatchInput`; Core owns classification, validity-first
aggregation, atomic publication, and strict reload of the resulting `BatchSummary`. The four
exhaustive Trial outcomes are:

- `pass`: selected evidence is valid and its behavioral decision passes.
- `behavioral_fail`: selected evidence is valid and its behavioral decision fails.
- `evaluation_invalid`: an Evaluation Package is present but invalid or inconclusive.
- `infrastructure_unavailable`: no Evaluation Package is available and the selected lifecycle
  evidence records an unavailable execution outcome.

An invalid Evaluation Package is never downgraded to `infrastructure_unavailable`. Earlier retry
Attempts remain diagnostic members of the same Trial; they do not increase the planned or observed
Trial count. Running `summarize` repeatedly from the same Pack into distinct new directories must
produce byte-identical authoritative summary bytes and the same content identities.

M2 has no comparison, delta, interval, pass-at-k, `pass^k`, ranking, promotion, improvement
decision, or winner. Those concepts are outside the `0.2.1` contract. See `batch-summary.md` for
the detailed boundary.

## Exit status

- `0`: verification, completed run/resume, rebuild, or batch summarization succeeded.
- `2`: command usage is invalid, an output is not new, or `--accept-plan-id` does not match.
- `3`: strict validation, normalization, or execution failed, or run/resume returned a
  non-completed status such as `stopped` or `budget-exhausted`.

The deterministic release gate runs the public offline 2 Cases × 2 Configurations × 3 repetitions
conformance matrix, including success, behavioral failure, timeout, eligible retry followed by
non-retryable terminalization, crash adoption, tamper rejection, and hard budget exhaustion. It
never invokes the live connector.

The separate manual exit gate uses `examples/priority4-m1-native-acceptance.json`. Its four cells
bind distinct ExperimentSpec identities for each Case under `normal-policy` and
`short-timeout-policy`, with three repetitions each. The operator must explicitly accept the
printed RunPlan identity, request one graceful stop after at least one completed Trial, resume the
same Execution, retain the completed Pack and rebuild, and verify that the result contains both a
strictly rebuildable Evaluation and genuine unavailable lifecycle evidence.
