# Skill diagnostics and controlled comparison (experimental)

The Skill path now supports offline evidence-linked diagnostics and a frozen
two-configuration comparison across one Case or a frozen multi-Case roster. Core owns Batch classification,
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

## Multi-Case study (additive Plan v2)

Use `examples/skill-capture/study-plan.json` with the same freeze and compare
commands. This is a **synthetic protocol example**, not a model benchmark.
Its `schema_version` is `cernora.reference.skill-comparison-plan/v2`.
The `cases` array contains sorted, unique Case IDs, each with a `split`,
`baseline`, and `candidate`. Both `development` and `workflow_check`
must be present. Object IDs cannot overlap between Cases; callers must also
ensure semantically related variants stay in the same group.

All configuration-wide settings must be identical across Cases within each
arm. Per-Case task, objects, facts, and dependencies may differ; within a Case,
both arms retain the exact same task and references. The entire roster binds
the shared authority, dataset identity, and required task metric before capture.
The task metric selects the appropriate frozen reference using the exact native
Plan, and import validates the selected Case rather than the first roster entry.

The primary outcome is reliable success on `workflow_check`. Hard Guardrails
cover overall evaluation validity and development success, allowing no adverse
change. The practical threshold is supplied in the Plan. Core performs the
paired Case-clustered bootstrap; repetitions do not create additional Case
clusters. Collection order is frozen by repetition and sorted Case, alternating
the first arm by Case index plus repetition. With an even number of Cases,
Baseline-first and Candidate-first pairs are balanced. This schedule alone is
not randomized causal identification.

Study evaluations use Profile/projection v3 and task metric v2. Single-Case
Plan v1 still uses Profile/projection v2 and the original task metric; standalone
evaluation remains v1. Old frozen Plans and evaluations retain their identities.
Study packages are new authorities and cannot replace historical receipts.

Keep exploration outside the comparison matrix. Select a candidate using only
development evidence, then freeze the roster, references, instructions,
statistics and order before collecting fresh attempts. Merely naming a split
`workflow_check` does not prove it was previously unexposed or that it represents
an independent real-world holdout. Retain the selection and exposure provenance.

Neither mode automatically promotes a candidate. Small synthetic studies cannot
establish population-level improvement. Token and duration diagnostics do not
constitute a billing-cost or cost-efficiency Gate.

Offline adversarial tests cover missing loading/usage/completion, tool and fact
errors, incomplete or duplicated matrices, freeze tampering, deterministic
rebuild, all six Core conclusions, split-specific primary outcomes, development
regression Guardrails, and cross-Case configuration/reference drift using labeled
synthetic evidence.
