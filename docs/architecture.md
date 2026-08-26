# Architecture

This repository is a companion workflow for evaluating frozen coding-agent evidence. Its
historical M1 surface uses the released `cernora==0.1.2` wheel; local M2 and M3 candidates use
accepted Core `0.1.3` and `0.1.4` wheels respectively. It does not extend Cernora Core with
Runtime, Harness, credential, orchestration, or publication responsibilities.

## Ownership and data flow

```text
ExperimentSpec v1
  -> Harbor 0.16.1 and Codex CLI 0.148.0 (live, local, manual)
  -> immutable attempt and task-owned Test Runner receipt
  -> completed-export/v1 (closed, hashed, secret-scanned)
  -> offline companion Adapter
  -> Cernora EvidenceBundle v2 and explicit companion Profile
  -> Cernora evaluation and strict reload
  -> run-report/v1 JSON plus derived Markdown
```

Harbor owns the task container and Runtime lifecycle. Codex owns Agent behavior. The task-owned
Test Runner is the only behavioral verdict authority. The companion exporter owns the closed
export boundary, while the Adapter performs a pure translation after export verification.
Cernora owns import, Profile evaluation, canonical result publication, and strict reload.

M3 adds a separate, additive controlled-comparison path:

```text
ControlledExperimentSpec v2 canonical sources
  -> Core ExperimentAuthority v1 identities before execution
  -> ControlledRunPlan v2 complete two-configuration matrix
  -> strict Core Batch Summary package
  + ComparisonPlan v1 exhaustive Treatment declaration
  -> Companion binding and projection verification
  -> Core ComparisonInput, publication, and strict reload
```

The V2 authority source is the source of truth for Runtime, model, Prompt/Instruction, tools,
generation, timeout, resources, retry, dataset, Profile, report, statistical, and Evaluation
projections. Caller-provided equal strings cannot establish comparability. Case-specific task
material is bound by the common dataset authority; the permitted Prompt/Instruction Treatment is
configuration-global. Any missing, incompatible, or undeclared authority fails closed before a
controlled conclusion.

Legacy ExperimentSpec/RunPlan v1 identities remain frozen and are never post-hoc rewritten into
Core authority identities. Core alone owns pairing, bootstrap intervals, pass-k metrics,
Guardrails, failure migration, and conclusion semantics. Companion assembles and verifies inputs;
it does not select a winner, rank Configurations, or promote a candidate.

The report is observational. It is never supplied to Cernora and cannot change an evaluation.
Its JSON form is authoritative; `run-report.md` is generated only from the already validated JSON
model.

## Portable report contract

`cernora.reference.run-report/v1` has a canonical SHA-256 `report_id` over every member except
`report_id` itself. It records:

- exact task, image, Harness, Runtime, model, wheel, Profile, and Adapter identities;
- every attempt in order, including predecessor and fixed-delay retry links;
- lifecycle outcome independently from evaluation validity and behavioral/gate decisions;
- referenced EvidenceBundle and strict-reload result identities;
- Test Runner authority and receipt digests;
- export, candidate-tree, and artifact-manifest digests;
- verified resource, duration, and token measurements, or explicit missing-data markers; and
- relative offline inputs and exact command argument vectors.

Unknown members, wrong pins, invalid identities, hidden or unauthorized retries, non-canonical
JSON, secret patterns, raw environment assignments, account identifiers, and host absolute paths
are rejected. A diagnostic that was not emitted or verified is represented as `status: missing`
with a versioned reason; it is not represented as zero or an empty file.

The checked-in schema is [`../schemas/run-report-v1.schema.json`](../schemas/run-report-v1.schema.json).
Pydantic validation remains the semantic authority because it additionally checks content
identity, retry relationships, conclusion consistency, units, and portability.

## Security boundary

Live authentication is supplied from outside the repository and is not an experiment identity.
Authentication files, their paths and digests, Runtime homes, environment dumps, account data,
raw transcripts, and host paths are prohibited from exports and reports. Provider egress is
allowed for Codex, web search is disabled, and no network-isolation claim is made.

Frozen adaptation, evaluation, strict reload, and report rendering perform no Runtime, Docker,
shell, Git, test, or network action. Commands in a report are data for reproducibility, not
instructions executed by the report module.

## Publication behavior

A report is published as one no-replace directory containing:

```text
run-report.json
run-report.md
```

The JSON is canonical UTF-8 without insignificant whitespace. Both files pass the deterministic
secret scan before an atomic, conflict-safe directory publication. An existing destination is
never replaced.
