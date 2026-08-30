# Controlled Study Architecture (Priority 4 Replacement)

This document defines the replacement architecture for Priority 4. It is a Companion-owned
application module for one durable controlled study, not a new Cernora Core Runtime API. It
deepens the existing Repeat Runner, Execution Pack, Batch Summary, and Comparison contracts into
one coherent workflow while preserving the released historical formats as read-only inputs.

## Boundary and public seam

The final common-root interface has exactly three operations:

- `prepare`: validate caller-owned intent, freeze all derived authority, and publish a durable
  prepared study without starting external work;
- `advance`: make one idempotent transition using durable state and at most one claimed external
  attempt; and
- `rebuild`: verify a closed diagnostic or evidence pack and reproduce its derived local outputs
  without credentials, network, Runtime, Docker, Git, shell, tests, or child processes.

Leaf contract types remain available from the `controlled_study` module for validation and
inspection. They are not promoted as independent orchestration APIs. Cernora Core remains
Runtime-neutral and continues to own Batch and Comparison semantics. Companion owns scheduling,
Runtime adaptation, custody, recovery, and Core input assembly.

The current `controlled_runner`, `controlled_execution_store`, and related live-attempt modules
are migration sources. Their verified receipt handling and failure classifiers may be reused,
but their parallel state model is not a second supported foundation and must not acquire new
public behavior.

The CLI mirrors that boundary under one command group:

```sh
experiment study prepare STUDY_INTENT.json --output NEW_STUDY
experiment study advance STUDY --directive ADVANCE_DIRECTIVE.json
experiment study rebuild TERMINAL_ARTIFACT --output NEW_REBUILD
```

Historical top-level Repeat Runner, Batch, and Comparison commands remain parsers for their frozen
M1–M3 artifacts. New controlled work must not compose those commands into a parallel workflow.

## Frozen authority before execution

`prepare` separates caller choices from derived protocol data:

1. `StudyIntent` records the Baseline, Candidate patch, causal hypothesis, development,
   regression, and held-out Case authorities, repetition and safety budgets, held-out manifest,
   analysis policy, and exact implementation lock.
2. `StudyProtocol` derives the complete repetition-major adjacent paired schedule, Trial count,
   claim scopes, and safety-only stop policy.
3. The prepared state and its ledger root bind the Intent and Protocol before any reveal,
   acceptance, credential use, or Runtime action.

`ImplementationLock` identifies exact bytes, not display versions, for Companion, Cernora,
Runtime adapter, Harness, and analysis policy. Any changed artifact digest creates a different
lock and therefore a different protocol authority. Acceptance is valid only for the exact
prepared identity; a later implementation or protocol change makes it stale.

The Candidate is a declared patch over one frozen Baseline. Its Treatment axis and Treatment
digest must be explicit, and the Candidate authority must differ from the Baseline authority.
Candidate development uses development evidence only. Regression Cases are guardrails. Held-out
Cases are not used to construct or revise the Candidate.

## Claims and schedule

Each Case and repetition forms one adjacent paired block containing Baseline and Candidate exactly
once. Pair order alternates AB/BA across Case and repetition coordinates to counterbalance local
order effects. The complete schedule is frozen before external work and cannot be shortened by a
quality observation.

Claim authority is split deliberately:

- development observations are descriptive;
- regression observations are guardrails;
- held-out observations are the confirmatory primary evidence; and
- a `contract-proof` study may complete the execution contract but its effect conclusion remains
  `descriptive-only`.

Only a `confirmatory-effect` protocol can request a confirmatory effect conclusion. Completion
does not mean improvement: it means the planned protocol and evidence closure succeeded. Core may
still conclude `not_comparable`, `uncertain`, `no_change`, `mixed`, or `regressed`.

Confirmatory execution has no quality-based early stop. It may pause or terminate only for the
frozen safety and integrity reasons: ambiguous active attempt, exhausted budget, integrity
failure, explicit operator request, or safety limit.

## Durable state machine and custody

The only supported state progression is:

```text
Prepared
  -> AwaitingReveal
  -> AwaitingAcceptance
  -> Running
  -> Paused | Completed | Terminated
```

Every transition appends a content-addressed, hash-chained ledger record before exposing the new
state. `advance` first reloads and verifies the ledger, then either returns the already-materialized
outcome or claims the next transition. Repeating the same call must not create a second Trial or
Attempt. An active Attempt without one verifiable terminal artifact becomes
`ambiguous-active-attempt`; it is never guessed or silently retried.

Execution stepping uses a two-entry handshake. `execution-step-claimed` binds the caller's prior
state and exact Execution snapshot before the Repeat Runner may claim work;
`execution-step-advanced` closes that claim after at most one external Attempt. If a process exits
between them, recovery may reconcile, adopt, and finalize progress under the existing claim, but
it is forbidden to start the next Attempt until that Study boundary is closed.

The durable study directory is authoritative custody. Scratch directories and Runtime workspaces
are replaceable staging areas and cannot be the sole home of a receipt, Attempt record, terminal
artifact, or ledger entry. External adapters may write only to a preassigned staging destination;
the study adopts verified bytes atomically into durable custody. Existing destinations are never
overwritten.

## Terminal evidence closure

Every terminal outcome is locally inspectable and offline rebuildable:

- `Paused` publishes a closed diagnostic pack and remains resumable;
- `Terminated` publishes a closed diagnostic pack and is not resumable; and
- `Completed` publishes a closed evidence pack containing the ledger closure, report, and strict
  Cernora Batch and Comparison packages.

Diagnostic packs have `diagnostic-only` authority and cannot contain authoritative Batch or
Comparison package identities. Completed evidence packs must contain both identities. A closed
manifest indexes every carried file by relative path, length, and digest and binds the protocol,
implementation lock, terminal status, claim authority, and ledger root. Missing, corrupt,
concurrently changed, or identity-mismatched content fails closed.

Structural failures use stable error codes qualified by `prepare`, `advance`, or `rebuild`.
Human-readable logs can change; automation branches only on the stable phase and code.

## Compatibility and migration order

Historical M1 RunPlan, Execution, and Execution Pack contracts remain readable and byte-stable.
Core Batch and Comparison packages retain their existing schemas and semantic owner. The
replacement is introduced in this order:

1. freeze the Controlled Study contracts, state machine, claims, and stable failures;
2. put execution, custody, adoption, pause, termination, and rebuild behind the single ledger;
3. enforce development/regression/held-out separation and acceptance freshness;
4. project a completed study into the existing strict Core Batch and Comparison inputs;
5. publish one closed artifact and one status view for every terminal state; and
6. prove the complete 9 Cases × 2 configurations × 3 repetitions path with offline fakes and
   deterministic fault injection before authorizing any live experiment.

There is no automatic migration of incomplete experimental directories into authoritative
studies. A migration tool must verify old artifacts, map them explicitly, and publish a new study
identity. Historical 54-Trial observations remain diagnostics unless they satisfy the new frozen
authority and closure rules.

## Non-goals

This architecture does not start a live experiment, expose credentials, add a generic Runtime
plugin SDK, move Runtime concerns into Core, infer implementation identity from version strings,
promote a Candidate, or turn incomplete evidence into a quality conclusion. General Runtime
extensibility belongs to a later Priority 6 decision after the single supported adapter path is
stable and evidenced.
