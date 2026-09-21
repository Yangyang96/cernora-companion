# Compatibility

This companion is pinned to one Priority 3 experiment authority. Pins are part of evidence
identity; changing one requires a new frozen experiment and compatibility record.

| Surface | Priority 3 contract |
| --- | --- |
| Python | CPython 3.12 and 3.13 |
| Cernora | exact public wheel `0.1.2` |
| Cernora input | `agent.evaluator.evidence-bundle/v2` only |
| Harbor | `0.16.1` |
| pi coding agent | `0.84.4` on Node.js `22.23.2` |
| Model | `deepseek/deepseek-v4-flash` via direct provider egress, thinking level `medium` |
| Primary task | `tiny-calculator-v1`, version `1` |
| Behavioral-failure task | `tiny-calculator-v2`, version `2`; separately versioned harder task |
| Profile | `cernora-reference-coding-v1`, version `1.0.0` |
| Adapter | `cernora-reference-adapter`, version `1` |
| Export | `cernora.reference.completed-export/v1` |
| Report | `cernora.reference.run-report/v1` |
| Initial live host | macOS on Apple Silicon, only after native acceptance |
| Offline CI role | Linux validation of frozen artifacts; no live Runtime support claim |

The Cernora package-root SDK is the only Cernora programming interface used by the companion.
Cernora internal modules and source-checkout imports are not compatible inputs.

## Priority 4 release-candidate layers

Priority 4 preserves the Priority 3 authority above and adds versioned layers without changing
the meaning or identity of existing artifacts.

| Layer | Companion | Cernora Core | Status and boundary |
| --- | --- | --- | --- |
| M1 Repeat Runner | `0.2.0` | public wheel `0.1.2` | Frozen historical execution, Pack, and rebuild contract |
| M2 Batch Summary | local candidate `0.2.1` | local candidate `0.1.3` | Additive strict Pack consumer; not publicly released |
| M3 Controlled Comparison | local candidate `0.3.0` | local candidate `0.1.4` | Additive authority-bound comparison assembly; not publicly released |

The M2 normalizer accepts one completed M1 Execution Pack, preserves its frozen RunPlan,
Execution, Trial, Attempt, Evaluation, and lifecycle identities as source authority, and creates a
strict Core `BatchInput`. Core alone owns the validity-first `BatchSummary` classification and
atomic publication contract. Repeated normalization of the same Pack must produce byte-identical
authoritative summary bytes.

M2's exhaustive Trial outcomes are `pass`, `behavioral_fail`, `evaluation_invalid`, and
`infrastructure_unavailable`. A present but invalid Evaluation Package remains
`evaluation_invalid`; retry Attempts remain diagnostic lineage rather than independent Trials.
The M2 surface contains no comparison, delta, interval, pass-at-k, `pass^k`, ranking,
promotion, improvement decision, or winner.

M3 adds `ControlledExperimentSpec/v2`, `ControlledRunPlan/v2`, and `ComparisonPlan/v1`. A V2
Experiment ID is the independently recomputable Core `ExperimentAuthority/v1` identity before
execution. Companion derives every comparison projection from the typed canonical sources and
requires the complete two-configuration Trial matrix to carry those exact identities. It does not
trust caller-declared arm digests or rewrite a legacy Experiment ID after execution.

Legacy M1/M2 RunPlans, Packs, and Batch Summaries retain their frozen v1 identities. They remain
valid for their original readers, but cannot be used as M3 controlled-comparison authority. This
is an additive boundary, not a migration. Core `0.1.4` owns pairing, fixed bootstrap statistics,
Guardrails, failure migration, and the conclusion; Companion publishes no winner, ranking, or
promotion decision.

## Runtime switch boundary

The live Runtime connector switched from Codex `0.148.0` to pi `0.84.4` as a new frozen identity,
not an in-place mutation: current strict reloaders (ExperimentSpec, RunPlan, ControlledRunPlan,
completed-export verification) accept only pi-era vocabulary, and RunPlan connector identity is
`cernora-reference-harbor-pi` version `2`. Codex-era artifacts — the private local exports,
attempts, and M1 acceptance Packs, and the checked-in `examples/m3-offline` fixture trio —
remain on disk and in Git history as frozen historical evidence, but they are no longer
loadable by the current contracts and must be verified with the codex-era revision of this
repository (the `images/codex-runtime` revision recorded in `docs/compatibility-record.md`).
A false promise of cross-era reload would be a weaker contract than this explicit break; the
pi-era equivalents of the M3 offline fixture and the native acceptance evidence are new
identities that must be frozen after the authorized pi live run.

## Report evolution

`run-report/v1` is a companion artifact, not a Cernora import format. Readers must reject unknown
schema versions and unknown members. Existing v1 members are not reinterpreted. A semantic or
structural break requires a new report schema identity and new frozen acceptance evidence.

The Markdown rendering has no independent compatibility authority. Consumers that make decisions
must read and validate `run-report.json`; Markdown may be regenerated from the same JSON model.

## Outcomes

Lifecycle, evidence validity, and behavioral decision are different dimensions:

| Evidence condition | Evaluation validity | Behavioral decision |
| --- | --- | --- |
| Complete, internally consistent passing receipt | `valid` | `pass` |
| Complete, internally consistent failing receipt | `valid` | `fail` |
| Missing, corrupt, mismatched, or unverifiable authority | `invalid` or `unavailable` | `inconclusive` |
| Secret detected before export publication | no completed export | no evaluation claim |

A Harness reward, pi message, or trajectory prose cannot upgrade any row.

The timeout variant keeps the v1 task authority but binds an explicit Harbor Agent timeout
multiplier of `0.01` and effective limit of 3 seconds into a distinct ExperimentSpec. Operator
interruption binds `operator_interrupt=true` into its own v1 Harness configuration and records a
real `SIGINT` receipt against the active pi process. Neither lifecycle is automatically
retryable.

## Unsupported scope

Windows, Intel macOS, remote Docker, Kubernetes, hosted live execution, arbitrary Runtime
connectors, automatic Profile discovery, telemetry that cannot be disabled, and broad sandboxing
claims are outside Priority 3. The workflow must remain private until every release gate is
accepted.

## Experimental Skill path

The opt-in `experiment skill` path uses pi 0.85.1 and the Core MetricPlan SDK
candidate. See [Skill capture](skill-capture.md); existing coding runtime pins
are unchanged. The checked-in ComparisonPlan schema now includes the accepted
Core split-scoped PrimaryOutcome shape; omitted split selection retains its
existing all-cases meaning. No historical Plan identity is rewritten.

Skill diagnostics retain the v1 Profile. Frozen Skill comparisons introduce an
additive v2 projection/Profile bound to both predeclared Plans; they do not rewrite
v1 evidence. The initial comparison slice is one development Case with no holdout
or billing-cost claim. See [migration and limits](skill-diagnostics-comparison.md).
