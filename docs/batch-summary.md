# Batch Summary (Priority 4 Milestone 2)

Companion `0.2.1` is the offline normalization boundary between one frozen Milestone 1 Execution
Pack and Cernora Core `0.1.3` Batch contracts. Both versions are local release candidates and have
not been publicly released. M1 remains governed by Companion `0.2.0` and the public Core `0.1.2`
wheel; M2 does not mutate, migrate, or reinterpret those frozen artifacts.

## Command

```sh
uv run experiment summarize /absolute/path/to/execution.pack \
  --output /absolute/path/to/new-summary
```

The source must be a completed, closed M1 Pack. The output parent must exist and the output path
must not exist or be a symlink. Success prints canonical JSON containing the command, RunPlan,
Execution, BatchInput, and Summary identities plus `status: completed`.

Exit status is intentionally narrow:

- `0`: strict normalization, atomic publication, and strict reload succeeded.
- `2`: command usage is invalid or the output path is not new.
- `3`: Pack verification, contract validation, normalization, publication, or reload failed.

The command performs no live Runtime work and requires no credentials, proxy, network, Docker,
Git, shell, or test execution.

## Normalization boundary

Companion verifies the Pack before reading it, strictly reloads the completed Execution, and
projects only declared authority into a Core `BatchInput`:

- the frozen RunPlan identity and exact ordered planned-Trial matrix;
- the completed Execution identity and budget status;
- every Trial's Case, Configuration, Experiment, repetition, and slot identity;
- each Attempt's ordinal, source identity, predecessor lineage, retry eligibility, and manifest
  receipt;
- the selected strict Evaluation Package when present, otherwise the selected strict lifecycle
  record; and
- available duration and token receipts only when their authority is consistent.

Normalized Attempt identities are content-derived and distinct from their frozen source Attempt
identities. Predecessors refer to those normalized identities, while the source identities remain
explicit bindings. Companion verifies the Pack again after projection so concurrent or partial
changes cannot be silently accepted.

Unknown fields, missing files, undeclared files, digest mismatches, conflicting token receipt
authority, malformed Evaluation Packages, invalid lifecycle states, inconsistent counts, or any
identity mismatch fail closed. The normalizer does not infer missing values or turn unavailable
resource data into zeroes.

## Validity-first Core summary

Core owns classification and aggregation. Every planned Trial has exactly one exhaustive outcome:

| Outcome | Meaning |
| --- | --- |
| `pass` | Selected Evaluation evidence is valid and its behavioral decision passes. |
| `behavioral_fail` | Selected Evaluation evidence is valid and its behavioral decision fails. |
| `evaluation_invalid` | An Evaluation Package is present but invalid or inconclusive. |
| `infrastructure_unavailable` | No Evaluation Package is available and selected lifecycle evidence records unavailable execution. |

This ordering is validity-first: a present invalid Evaluation Package cannot become
`infrastructure_unavailable`. Retry Attempts are diagnostics inside one Trial lineage and never
count as independent Trials. Resource totals retain explicit unavailable values instead of
inventing measurements.

Core publishes the BatchInput, Summary, manifest, and supporting resources atomically and strictly
reloads the result. Summarizing the same verified Pack into three distinct new destinations must
produce identical authoritative bytes and identical content identities.

## Explicit non-goals

M2 describes one Execution. It does not compare Configurations or Treatments and contains no
delta, confidence or percentile interval, p-value, pass-at-k, `pass^k`, ranking, promotion,
improvement decision, or winner. Downstream tools must not infer those claims from M2 counts.
