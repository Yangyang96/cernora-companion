# Skill diagnostics and controlled comparison (experimental)

The Skill path now supports offline evidence-linked diagnostics and a frozen
single-Case, two-configuration comparison. Core owns Batch classification,
statistics, Guardrails, and all six comparison conclusions. Companion supplies
validated captures and links those conclusions to Trial diagnostics.

## Diagnose a completed capture

```sh
uv run --no-sync experiment skill diagnose /absolute/path/to/native-export \
  --plan /absolute/path/to/skill-plan.json \
  --output /absolute/path/to/new-diagnostics
```

The report preserves the original task outcome and strict Core evaluation.
`diagnostics.md` links to the metric report and native evidence. `diagnostics.json`
contains the original receipt/report, invocation IDs, request indices, source
hashes and line locators. `native/` is a verified copy of the original capture.
All of these artifacts can contain private content; keep them private by default.

Observations distinguish loading, tool errors, answer format, mismatched facts,
required task constraints, completion evidence, and accounting gaps. A tool error
is an observation even when the task succeeds. Missing loading cannot overwrite a
valid task result. Any suggested cause or instruction change is an unverified
hypothesis, never a finding that the Skill caused the failure. The original
answer is neither repaired nor replaced for scoring.

## Freeze before collecting comparison Trials

```sh
uv run --no-sync experiment skill freeze-comparison \
  examples/skill-capture/comparison-plan.json \
  --output /absolute/path/to/new-freeze.json
```

The input contains complete Baseline and Candidate Skill Plans, equal repetition
counts, a purpose, and a practical effect threshold. Both arms must retain the
same Case, task, objects, references, dependency, and business tool identity.
The example is explicitly `synthetic_validation`; its records describe a neutral
catalog, not production data or model-improvement evidence.

The freeze derives shared scoring authority, configuration projections, declared
Treatment changes, statistical policy, and every Trial slot before evidence is
read. It performs no model call and grants no external-send authorization.
For a prospective experiment, review and retain the freeze before collecting
fresh independent attempts using each slot's exact Skill Plan. A freeze file is
not cryptographic proof of collection chronology; the collector and provenance
record remain trusted inputs. Historical exploratory smoke cannot be promoted to
controlled evidence by authoring a freeze afterward.

Create a `sources.json` object mapping every frozen `trial_slot_id` to its native
export directory, then run:

```sh
uv run --no-sync experiment skill compare /absolute/path/to/freeze.json \
  --sources /absolute/path/to/sources.json \
  --output /absolute/path/to/new-comparison
```

There must be exactly one completed capture per slot. Missing/extra slots,
substituted Plans, duplicated native attempts, altered freeze identities, and
invalid native evidence are rejected. Diagnostic and comparison publication is
atomic; a failed assembly does not leave a final report directory. This offline
path does not create another scheduler or retry policy.

## Read the result

`report.md` links the authoritative Core Comparison and Batch to per-Trial
findings. Each Trial includes an unchanged answer, strict evaluation, metric
references and native trace. `report.json` embeds the Core summaries, rather than
recalculating or overriding their statistics. Core can return `improved`,
`no_change`, `mixed`, `regressed`, `uncertain`, or `not_comparable`; these are valid
results, not command failures. A zero estimated difference can be `uncertain`
under Core's interval rule; Companion does not relabel it.

Runtime/model/instruction/tool/generation differences produce explicit Treatment
endpoints. Differences in invariant limits, such as timeout, remain visible and
produce Core's `not_comparable` result. Wrong references or Case swaps are
rejected before comparison instead of normalized into common identities.

Duration is supplied as verified process evidence. Total runtime tokens and
partial token observations remain per-Trial diagnostics. Input/output breakdown
and billing cost are unavailable in the current normalization, rather than
invented from totals. No cost-efficiency Gate is claimed. `pass@k` is disabled;
this path makes no independence assertion beyond the declared collection scope.

## Compatibility and limits

Standalone diagnostics use the historical v1 Profile. Comparison uses a new v2
projection/Profile with one shared authority bound to the entire frozen two-arm
roster. Both arms have the same source-bound task metric; their native captures
are still validated against their distinct exact Plans. Existing v1 evaluations
and identities remain unchanged. V2 evaluation creates new packages and must not
be substituted for old receipts.

The current vertical slice covers **one development Case** with predeclared
repetitions. It is not a multi-Case holdout study, automatic promotion system,
causal diagnosis, or general Runtime connector. Single-Case intervals cannot
establish population-level improvement. A later experimental stage must add the
intended dataset/split protocol rather than extrapolate this demonstration.

Offline adversarial tests cover missing loading/usage/completion, tool and fact
errors, incomplete or duplicated matrices, freeze tampering, deterministic
rebuild, and all six Core conclusions using labeled synthetic evidence.
