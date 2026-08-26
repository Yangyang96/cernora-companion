# Companion Repeat Runner (Priority 4 Milestone 1)

The Companion Repeat Runner is the `cernora-reference-workflow==0.2.0` orchestration boundary. It
is not Cernora Core, a generic Runtime connector, or a native batch service. Its only live adapter
is the source-tree-only qualified Harbor `0.16.1` / Codex `0.148.0` connector already used by this
repository.

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

## Exit status

- `0`: verification, completed run/resume, or rebuild succeeded.
- `2`: command usage is invalid, an output is not new, or `--accept-plan-id` does not match.
- `3`: strict validation or execution failed, or run/resume returned a non-completed status such
  as `stopped` or `budget-exhausted`.

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
