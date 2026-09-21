# Single-attempt Skill capture (experimental)

`experiment skill` connects one pi attempt with a frozen Skill and read-only
snapshot replay to a Core MetricPlan evaluation. Agent execution stays in this
companion; Core consumes only completed exports. This command does not schedule
an experiment, assemble a Batch, compare configurations, or promote a Skill.

This opt-in path requires pi **0.85.1** and an installed Core wheel containing
`MetricPlan`, `MetricContext`, and `ToolSelection`. The local Core candidate still
reports `0.1.4`; that version string alone does not establish compatibility.
Build/install the accepted Core source artifact and record its wheel digest.
Use `uv run --no-sync` after installing that wheel so dependency resolution does
not replace it. The historical Harbor coding path retains its own runtime pins.

## Review and execute

The neutral example includes two synthetic catalog objects. It is not production
service evidence. Its extension digest pins the collector shipped in this tree.
Changing collector bytes requires a newly reviewed Plan.

```sh
uv run --no-sync experiment skill inspect examples/skill-capture/plan.json
uv run --no-sync experiment skill capture examples/skill-capture/plan.json \
  --auth /absolute/path/to/pi-auth.json \
  --accept-plan-sha256 <reviewed-plan-sha256> \
  --output /absolute/path/to/new-native-export
uv run --no-sync experiment skill evaluate /absolute/path/to/new-native-export \
  --plan examples/skill-capture/plan.json \
  --output /absolute/path/to/new-evaluation
```

Capture sends the effective instructions, Skill content that is expanded/read,
tool definitions, replay outputs, and conversation to the declared provider.
Inspect the entire Plan before accepting its digest; `inspect` is an inventory,
not an authorization grant or a content redactor. The current transport supports
only DeepSeek's declared endpoint and API-key authentication. Auth files are not
exports; only the selected provider credential is copied into a temporary home.
There is no monetary hard cap. Request count, request bytes, output tokens, tool
calls, and process wall time are bounded; automatic retries and compaction are
disabled. Failed attempts retain their evidence and must not be silently replaced.

`invocation: explicit` expands `/skill:name`; `implicit` exposes the same Skill
for discovery via `read`. Both paths are permitted. Installation or discovery
alone does not count as loading: capture must show the frozen body delivered in
an actual request, or all its lines delivered through read results. Referenced
resource ranges are reported separately. Loading, task correctness, and usage
completeness are separate observations.

## Evidence and scoring

The closed export contains a frozen Plan, native pi events, actual provider
request bodies and response statuses, tool receipts, process receipt, and a
file-digest manifest. It is private by default: requests can contain sensitive
Skill and business content. No headers or credentials belong in public fixtures.
These hashes detect inconsistency, not forgery by a party controlling the entire
capture; the collector and selected Plan remain trusted inputs.

The Adapter verifies native tool calls against deterministic replay, checks
preceding outputs were delivered on later requests, and preserves the exact final
answer in a wrapper alongside native evidence. The Profile re-audits that evidence
after import; it cannot substitute reference values for the model answer. A
required `SnapshotTask` metric checks declared facts, source citations, and an
optional first-object-to-next-object dependency. Required `ToolSelection` and
diagnostic `ToolCalls` use Core's reusable metrics.

A complete wrong or malformed answer fails. Interrupted/missing completion is
inconclusive. Invalid captures are rejected rather than scored as successes.
Missing usage stays unknown without changing a valid task outcome. Runtime cost
estimates are not billing evidence and their currency is unverified. Truncated
answers retain their actual stop reason and are not repaired.

Snapshot replay is a deliberately finite CLI surface: it supports declared
read-only query arguments, help, multiple objects, and error responses. It does
not execute a shell or claim full CLI/service compatibility. Source digest,
pointer, and projection version describe provenance; derived views of one source
are not independently collected responses.

Offline tests include adversarial exports, missing usage/loading, repeated strict
reload, and both loading paths through installed pi with synthetic SSE responses.
Those integration checks skip when pi 0.85.1 is absent. They prove plumbing, not
real model behavior. Live acceptance must be recorded separately for the reviewed
Skill, snapshot, model, and bounds.

## Diagnostics and comparison

Use `experiment skill diagnose` for evidence-linked observations and
`freeze-comparison` / `compare` for the experimental one-Case two-arm workflow.
See [contracts, migration, and limits](skill-diagnostics-comparison.md). These
commands are offline and never send a new provider request.
