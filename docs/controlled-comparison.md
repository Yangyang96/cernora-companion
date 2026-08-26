# Controlled Comparison (Priority 4 Milestone 3)

Companion `0.3.0` and Cernora Core `0.1.4` are matching local release candidates. Neither has
been publicly released. The companion assembles strict Core comparison input; Core alone derives
statistics, conclusions, authoritative JSON, deterministic Markdown, and the closed output
package.

## Command

```sh
uv run experiment compare /absolute/path/to/batch-summary \
  --run-plan /absolute/path/to/controlled-run-plan.json \
  --plan /absolute/path/to/comparison-plan.json \
  --output /absolute/path/to/new-comparison
```

The source Batch Summary must be a strict Core package containing one completed `BatchInput` for
the exact controlled RunPlan. The output parent must exist and the output path must be new.

- `0`: a valid comparison package was published and strictly reloaded, including an honest
  `no_change`, `uncertain`, `mixed`, `regressed`, or `not_comparable` conclusion.
- `2`: command usage, selection, or predeclared comparison authority is incompatible.
- `3`: an input or output package is incomplete, corrupt, unstable, or unverifiable.

The command is offline. It performs no Runtime, network, credential, proxy, Docker, shell, Git, or
test execution.

## Pre-run identity boundary

Legacy M1/M2 `ExperimentSpec/v1` identities hash their complete historical specs. They do not equal
Core's neutral `ExperimentAuthority/v1` identities and are therefore rejected by this command.
Companion never rewrites those frozen Batch identities after execution.

Controlled `ExperimentSpec/v2` is additive. Before execution it binds typed canonical sources for
the Dataset, configuration-global Prompt/Instruction, Runtime, model, tool schema, generation
configuration, timeout, resources, retry policy, Profile, expected evaluation authority/policy,
report contract, and statistical policy. Companion rehashes those sources into the complete Core
projection; the resulting Core authority digest is the Experiment ID used by the V2 RunPlan and
every Trial slot from the start.

The V2 RunPlan requires exactly two Configurations, an exhaustive sorted Case × Configuration
matrix, equal predeclared repetitions, sequential execution, and the complete worst-case Attempt
budget. Case-varying task material belongs to one shared Dataset authority. Configuration-global
projection fields must remain coherent across Cases. An opaque Runtime configuration is bound
conservatively to both tool-schema and generation-configuration projections so a hidden change
cannot masquerade as a prompt-only Treatment.

## Assembly and Core authority

`ComparisonPlan/v1` binds one V2 RunPlan, ordered Baseline and Candidate Configurations, exhaustive
Case splits, a nonempty Treatment declaration, Reliable Success Rate Primary Outcome, hard
Guardrails, and the fixed independent statistical policy. Companion derives Treatment endpoints
from the V2 authorities and requires the declared kinds to exhaust every arm difference.

For Trials with Evaluation Packages, the embedded strict receipt must equal the precomputed
evaluation authority and policy. Missing Evaluation evidence remains an infrastructure outcome;
it does not permit caller-authored authority. Any Batch slot, RunPlan, Experiment, receipt, Profile,
policy, or statistical mismatch fails closed or produces Core's descriptive `not_comparable`
result as appropriate.

Core pairs Trials by Case and repetition, never Attempts. It calculates Reliable Success Rate,
the fixed case-clustered paired bootstrap interval, qualified `pass@k`/`pass^k`, hard Guardrails,
outcome transitions, and authority-compatible failure migration. The companion does not calculate
or override those facts.

## Non-goals

M3 does not run the final nine-Case experiment, choose a Candidate, rank Configurations, declare a
winner, auto-promote an artifact, or make a deployment decision. Those actions are not encoded in
the comparison package. Milestone 4 supplies one separately frozen improvement-loop execution and
publishes every result locally, whether it improves, changes nothing, remains uncertain, mixes
gains and regressions, or regresses.
