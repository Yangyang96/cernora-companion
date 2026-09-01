# Live Controlled Study Dossier

Status: **template only — no live study is authorized**

This document is the mandatory pre-execution dossier for the next authenticated Priority 4
Controlled Study. Completing the prose is not authorization, and this file is never an acceptance
token. The canonical `StudyIntent`, derived `StudyProtocol`, `ControlledRunPlanV2`, and
`ComparisonPlanV1` remain the machine-verifiable authorities.

Create one copy of this template for each proposed live study. Do not reuse the retired Candidate,
previously revealed held-out material, acceptance IDs, execution nonces, plans, or live artifacts.

## 1. Administrative status

Fill every item before requesting live authorization.

| Field | Required value |
|---|---|
| Dossier status | `draft`, `offline-reviewed`, `reveal-authorized`, then `live-authorized` |
| Study purpose | One falsifiable sentence |
| Study mode | `confirmatory-effect` or `contract-proof` |
| Owner | Named operator responsible for the complete study |
| Independent reviewer | Person or review task that did not develop the Candidate |
| Custodian | Owner of the held-out commitment and reveal procedure |
| Planned live window | Explicit date/time and timezone |
| Authorization record | Separate user approval, recorded only after offline review |
| Authorization scope | Exact study/protocol/plan IDs and maximum external Attempts |

Until the status is `live-authorized`, no command may use Runtime credentials, provider proxy
settings, Docker execution, a live Attempt adapter, or `experiment study advance` with
`step-execution`.

## 2. Scientific question and claim boundary

Record before Candidate construction:

- Baseline behavior and the specific observed limitation;
- one causal hypothesis for why the Candidate should change that behavior;
- exactly one declared Treatment axis and the intended patch;
- the confirmatory Primary Outcome;
- practical threshold, confidence method, and pass-at-k policy;
- regression and evaluation-validity Guardrails; and
- the outcomes that do **not** authorize an improvement claim.

Required claim boundary:

- development Cases are descriptive only;
- regression Cases are Guardrails only;
- held-out Cases are the only confirmatory Primary scope;
- protocol completion is not evidence of improvement by itself; and
- `uncertain`, `no_change`, `mixed`, `regressed`, or `not_comparable` remain valid analytical
  outcomes and must not be rewritten as success.

For `contract-proof`, the terminal claim authority must remain `descriptive-only`, even when all
Trials complete.

## 3. Fresh Candidate Development record

The Candidate must be developed without held-out task bodies, outputs, or qualitative hints.
Record:

| Authority | Required evidence |
|---|---|
| Baseline | Canonical authority ID and content digest |
| Candidate patch | Canonical patch bytes, digest, allowed Treatment axis |
| Candidate hypothesis | Frozen hypothesis ID and exact text |
| Development corpus | Case IDs, authority digests, and `development` split |
| Regression corpus | Case IDs, authority digests, and `regression` split |
| Development observations | Complete content-addressed records |
| Real failure basis | At least one matching authoritative Agent failure |
| Candidate freeze | Freeze ID proving the patch predates held-out reveal |

Reject the proposal if Candidate selection used held-out content, if the patch changes an
undeclared axis, or if the Candidate is not a patch over the frozen Baseline.

## 4. Fresh held-out custody

The custodian prepares a new held-out set independently of Candidate Development. Before reveal,
record only:

- held-out manifest digest;
- ordered Case commitment root;
- commitment ID;
- Case count and split label, without task bodies;
- custody root and owner;
- creation and access audit; and
- the exact reveal condition.

The reveal condition is: the Candidate, hypothesis, implementation lock, claim policy, schedule
rules, budgets, and analysis policy are already frozen in the prepared Study.

The reveal must match the commitment, manifest, exact held-out Case authorities, and order. Any
mismatch terminates preparation. Never patch the Candidate after reveal; create a new study with a
new Candidate and a new held-out set instead.

## 5. Implementation lock

Record exact artifact bytes, not display versions:

| Component | Version/ref | SHA-256 or immutable digest |
|---|---|---|
| Companion source/wheel | TBD | TBD |
| Cernora Core source/wheel | `0.1.4` candidate or successor | TBD |
| Runtime adapter | TBD | TBD |
| Harness | TBD | TBD |
| Analysis policy | TBD | TBD |
| Runtime image | TBD | immutable image digest |
| Per-Case task images | TBD | immutable image digests |

Also record platform qualification, Runtime model, reasoning effort, tool schema, generation
configuration, timeout, resource limits, retry policy, and report contract. A changed byte or
configuration requires a new `ImplementationLock`, `StudyProtocol`, acceptance ID, and plans.

Credentials, proxy URLs, account identifiers, host paths, and raw Runtime homes must not appear in
the lock or dossier.

### Offline preparation bundle before authority materialization

The repository carries one new closed review worksheet at
[`preparations/next-priority4-controlled-study`](../preparations/next-priority4-controlled-study).
Its `manifest.json` binds the exact current Core and Companion wheel candidate bytes and the files
in the worksheet. The held-out-only analysis policy and 9 × 2 × 3 / 54-Trial / 108-Attempt /
43,200-second values are explicitly marked as a non-binding `confirmatory-effect` recommendation;
they are not frozen Study semantics. Its status is `awaiting-user-decisions`: it deliberately does
not contain a `StudyIntent`, reveal, acceptance, execution nonce, RunPlan, ComparisonPlan, or
execution directive. The `verify` operation is strict only when both exact wheel artifacts are
provided; structural inspection alone does not claim artifact verification.

Strictly verify a bundle without Runtime or credentials:

```sh
PYTHONPATH=src python scripts/create_study_preparation_bundle.py verify \
  preparations/next-priority4-controlled-study \
  --companion-wheel /absolute/path/to/cernora_reference_workflow-0.4.0-py3-none-any.whl \
  --cernora-wheel /absolute/path/to/cernora-0.1.4-py3-none-any.whl
```

To propose another preparation, run the same script with `create`, both exact wheel paths, both
explicit expected versions, and a new output directory. Versions are preparation inputs rather
than compile-time product limits, so a reviewed successor can be bound without changing this
tool. Creation generates a fresh preparation nonce; it does not generate any live or Study
authority. This repository-maintenance script does not extend the public
`experiment study prepare` / `advance` / `rebuild` interface.

The current bundle cannot advance until the user supplies a falsifiable scientific question and
selects a genuinely new Candidate, independent reviewer, custodian, and new opaque held-out
commitment; locks the remaining Runtime, Harness, image, platform, and configuration bytes; and
names the owner, custody location, live window, and authorization scope. Once those inputs exist,
replace the preparation rather than filling canonical contracts with placeholders.

The evidence gap and the recommended next bounded development-only work package are recorded in
[`next-priority4-study-decision.md`](next-priority4-study-decision.md). That proposal is also
non-authoritative; its selected work package stops before any Agent pilot or held-out action.

### Historical development-only pilot boundary

The user selected `confirmatory-effect` on 2026-08-31. The first closed development-only bundle was
[`preparations/next-priority4-development-pilot`](../preparations/next-priority4-development-pilot),
with bundle ID
`80cbc4aac215d16b7e3f4adcdb5f276250ac064bdff64afdb8553e629bb8d51c`, plan ID
`273b259f2f0528dbccee2808c5e42e73173841b2b313f13bf84c467369710c43`, and authorization-request
ID `0ae0c5a7f65a0444decdb32d25ac99a1851f710dd87c08cf3e2898a058cea1ad`.
Its six fresh visible Cases are three `development` and three `regression` Cases; it contains no
held-out material. After authorization, custody execution
`46004fc1f812cbef2edfc5534a5a6e133a35ef85b05c9c4e5420722c5b8cd2a8` recorded
`execution-started` and one `attempt-claimed` event but no terminal Attempt artifact. Its outer
command returned exit code 1 after 304.913924 seconds; the observer-handle loss did not establish
host-process disappearance. The claim remains permanently ambiguous and cannot be retried,
resumed, or used as Agent evidence.

This historical boundary permits no further action. It authorizes neither a retry nor held-out
access or reveal, smoke execution, `start-execution`, `step-execution`, or any Trial in the future
54-Trial matrix. Candidate construction remains impossible without a matching authoritative
behavioral failure.

### Recovery development-only pilot boundary

The replacement bundle is
[`preparations/next-priority4-development-pilot-recovery`](../preparations/next-priority4-development-pilot-recovery),
with bundle ID
`a826de5070aed79a143081cf3b9d22b69190399c88e8addec4f792c3df686cba`, plan ID
`3d5a11931a3aa9ddfcf8ab3a9052dd17d43512726e68829a89429019b54ca223`, and authorization-request
ID `df60c04812b9e1755848fe0c61face5d45bfa1294dd3b874023e1caa12f585fd`. Plan v2 binds the exact
Core and Companion wheel candidates. The new custody execution
`c655a8db6dc3fa5014049f5fcafd57d3db0f7f4546e077234fc649925e67be96` was exactly authorized for
the complete development-only pilot. It recorded `execution-started` and one `attempt-claimed`
event for ordinal 1, slot 1. The outer command returned exit code 1 after 302.364721 seconds.
Offline reproduction identified a deterministic false-positive private-value scan: the CLI passed
the full ambient environment, and the executor treated every value as a proxy endpoint, so normal
output containing values such as `1` failed before terminal lifecycle publication. The equal
300-second Agent and outer Attempt timeout was a second boundary defect. Offline custody replay
reports `status=running`, zero published Attempts, zero completed Trials, and no outcome.

The recovery pilot therefore stops as `inconclusive`. Its outstanding claim is permanently
ambiguous and cannot be retried, resumed, converted into lifecycle evidence, or used as an Agent
observation. No later Trial was started. The authorization covered no held-out access or reveal,
smoke execution, or Trial in the future 54-Trial matrix.

Independent read-only and adversarial reviews examined the implementation from `d3109df` through
the final working-tree snapshot. Strict-tree, orphan-artifact, process cleanup, duplicate-custody,
self-consistent rewrite, and contradictory-request findings were fixed and re-reviewed; no
unresolved P0-P3 remains. The exact post-fix implementation passed 572 offline tests, Ruff, format
checking, strict Mypy, byte-identical dual wheel builds, reproducible wheel/sdist builds, license
inventory, and repository/archive secret scans. This review satisfies only the development-pilot
authorization boundary; it is not Candidate, held-out, reveal, smoke, or live-study review.

### Repaired development-only pilot boundary

The repaired bundle is
[`preparations/next-priority4-development-pilot-repair`](../preparations/next-priority4-development-pilot-repair),
with bundle ID `197d4bf9cf25b902a0706d978f242c5d5f325e9659ff5934ad1fbcd9abcd77d4`,
Plan ID `33544aa6d8ec292daf8b69390c4731ee67ee70184f2e7cd788c3baeeb4824099`, and
authorization-request ID `d34dfab521aeadb749d06f8d37b3df27a6eca4d0977374f88e27f39d65ed1c26`.
Its prepared execution is `bb5e9c7b87650a788c0eca899329110fd25c2ef6421ec4865c894cb5e9945ebf`;
the request binds physical custody path digest
`7ee5eb85c6c136af2169805efdb5166a1d07816d2336da54d90078c0446430fb`.
Plan v3 freezes a 300-second Agent timeout inside a 360-second Attempt envelope, scans only the
selected proxy endpoints, cleans the process group on operator signals, records value-free
incidents, and can ledger-adopt only the exact artifact for an active claim without rerunning it.
Custody stores the canonical request and replays its Plan, ordered Case authorities, envelope,
request ID, and physical path before any claim.
The user authorized the exact request, and execution completed all six Trials with exactly six
Attempts. Outcome `f381df0db6cdcbea00d6bd8f850100a5b519a171f92d6c38757dc91d7a56a08b`
is `inconclusive`. Every Attempt published a non-retry `runtime-pre-terminal-failure` lifecycle
artifact with no Runtime observation, repair result, evaluation, or Agent observation. Recorded
Attempt durations range from 316,149 to 319,974 milliseconds, and total execution elapsed time is
2,130,235 milliseconds, within the frozen 7,200-second wall bound. Strict replay finds six
claim/publication pairs, one completion, and no ambiguous claim, orphan artifact, or incident.

The lifecycle artifacts are not behavioral failures and cannot support Candidate construction.
No smoke, held-out access or reveal, Controlled Study execution, or 54-Trial work has started or
is authorized. The repaired development-only pilot stops at this completed `inconclusive` outcome.

Post-completion focused tests, static checks, repository/custody secret scans, and exact
credential-value absence scanning passed. Independent read-only replay recomputed every ledger,
artifact, request, Plan, execution, outcome, and physical path binding, found no unresolved P0-P3,
and recorded custody-tree snapshot digest
`8caac73411d024dde246914b2654c7cddd7785044c89f982a95477702efe2ff9`.

Subsequent offline diagnosis found that the live classifier could read Harbor's initialized
`agent_result` before reading an exact `AgentTimeoutError`, which can collapse a valid timeout into
the generic lifecycle observed in the six Attempts. The strict classifier fix, the inconclusive
one-shot confirmation, and the limits of the historical evidence are recorded in
[`runtime-pre-terminal-diagnosis.md`](runtime-pre-terminal-diagnosis.md). At that point, the
proposed one-Trial development-only follow-up was not yet authorized and could not supply Candidate
Development or Study evidence.

Its dedicated one-shot control plane is implemented offline and does not reuse the six-Case pilot
Plan or authorization. It requires fresh exact Plan/request acceptance, publishes at most one
diagnostic-only terminal artifact, and permanently stops after the first claimed Attempt. Plan
`6a342640911cade0ed3bd381e3ff80e0327d5230817a72ef6bac5d46e8d8bd4a` and request
`5b6bb9b88e8923cac40a5924597dbe5d9f9faa879731d307edfb8013034fba42` were exactly authorized
and fully consumed by one Attempt. It published a complete non-retry terminal artifact but stayed
`runtime-pre-terminal-failure`, leaving the timeout hypothesis unconfirmed. Offline repair now
durably retains only a fixed value-free mismatch code; any follow-up requires a new independently
reviewed wheel and fresh exact Plan/request authorization.

That follow-up was subsequently authorized exactly as Plan
`b039fa42eafc1a85be6e79bbbb4952639f64d8b4b89d3f62184b68838058ff76` and request
`8f9243aef0b8d03cc2733a9a57ab7195699469d806e3d3d0cf9a046c47016149`.
Its sole non-retry Attempt completed and durably recorded diagnostic code
`job-config-authority-rejected`. Offline execution of the exact argv through Harbor 0.16.1's real
configuration parser then proved that the companion validator mixed Job-level and normalized
Trial-level agent/task representations and imposed ordering on a set-backed retry field. That
systemic publication root cause is repaired offline. The authorization is fully consumed and
remains outside every Candidate, held-out, smoke, and Study boundary; live confirmation of the new
wheel requires a fresh exact one-shot authority.

The new closed confirmation proposal binds Companion wheel
`9bfe8711049241a368e6259c9b68fa2759e94b297fa140f2e11edc98d28081c7`, Plan
`45fc67a38cb662fea6bab5a5deead3049b93005c18215e0e8c5dec725e991e0b`, and request
`6ce143b2774fd21ece87c4144a12db81b0147f2de5d4b1ce16d6c32366f27f75`.
At materialization it was unprepared and unclaimed; its existence granted no execution authority.

That exact authority was subsequently consumed by one 318,513-millisecond Attempt. The repaired
JobConfig gate passed live, after which the value-free discriminator advanced to
`preterminal-structure-rejected`. Offline construction through Harbor's real TrialConfig boundary
proved the remaining deterministic mismatch: the validator expected a normalized custom Agent,
but Harbor preserves its unresolved Job-level `name`, `import_path`, and `n_concurrent` values in
the Trial. The Trial validator is repaired offline and now has a dedicated fixed mismatch code.

The new closed confirmation proposal binds Companion wheel
`6587da0fd9841a7e9815ec136db3a60e6329c375f6f12645dea7812faa0132c8`, Plan
`af27b05cbc54b6649856bac996106c21794de3f769bb75f9b98d50dfed87f2bd`, and request
`18ecdf5e3778dbef994fa9733e17cf012bcc82e71c0bd5fef0681dfb07e3763a`.
That authority was subsequently consumed by exactly one 318,294-millisecond Attempt. Both
configuration gates passed and the value-free discriminator reached
`agent-timeout-verifier-result`, confirming the live `AgentTimeoutError` path while leaving the
diagnostic outcome `inconclusive`. Offline Harbor reproduction proved the remaining classifier
defect: successful post-timeout evaluation was incorrectly required to retain an already proven
timeout. Missing Verifier results now remain non-evaluated but classify as `timed_out` only when
all independent timeout and timing evidence agrees. A fresh confirmation requires new wheel bytes
and new exact Plan/request authority. The closed proposal at
`preparations/next-priority4-runtime-timeout-classification-confirmation` binds Companion wheel
`ea0f48a6a0db3e539b7b2b55942c72d677aabc8f9984cf57068750958b4dee5e`, Plan
`a31414dfcaef392ffe33a648f715aff9d2ce55812ee066064fcbe646759adcfe`, and request
`e368f68cecceb854d2947149ab6c7ad4e230c45549d9aef00fa92b42981b742a`.
At materialization it was unprepared and unclaimed; its existence authorized no execution. The
user subsequently authorized it, and its sole 315,506-millisecond Attempt closed as non-retry
`timed_out` with `agent-timeout-evidence-accepted`. The authorization is consumed, the terminal
evidence is usable for lifecycle diagnosis only, and no Runtime observation, RepairResult,
evaluation, Candidate, smoke, held-out, or Study evidence was produced.

### Proxy-diagnostic follow-up boundary (2026-09-02)

Two further one-shot diagnostics ran on the same `p4-dev-json-pointer` Case through a locally
selected HTTP/SOCKS proxy. Both remain outside every Candidate, smoke, held-out, and Study
boundary, and neither supplied an Agent observation:

- 300-second Agent timeout inside a 360-second envelope: the provider attempt completed, but the
  closed result failed controlled serialization (`ControlledAttemptSerializationError`,
  classification-recoverable=false) before any Attempt artifact could be published. The claim
  closed as an incident with no terminal Attempt; the run is `inconclusive` and its authority is
  consumed.
- 600-second Agent timeout inside a 660-second envelope: the sole non-retry Attempt closed as
  `timed_out` (`agent-timeout-evidence-accepted`) after 617,761 milliseconds, with no Runtime
  observation, RepairResult, or evaluation. The classifier repair remains live-confirmed, and the
  Agent still does not complete within 600 seconds.

The first result is the only retained diagnostic record in which the provider attempt is marked
completed; the serialization boundary it exposed is not yet diagnosed. The driving command used a
`proxy-diagnostic` schema family that is not preserved in the checked-out source tree, so these
runs are recorded from their custody artifacts. No proxy endpoint, credential, or Runtime home is
recorded here. Before any follow-up proposal, the timeout and wall budget must be re-derived:
600-second Attempts at the 108-Attempt bound exceed the frozen 43,200-second study budget
arithmetic.

## 6. Offline freeze sequence

This sequence performs no live Attempt:

1. Build and independently review the fresh Candidate Development record.
2. Obtain the opaque fresh held-out commitment from the custodian.
3. Materialize and strictly validate the canonical `StudyIntent`.
4. Run only `experiment study prepare` into a new durable, non-temporary custody directory.
5. Strictly reload the resulting `StudyIntent`, derived `StudyProtocol`, Study record, and ledger.
6. Verify implementation digests, disjoint claim sets, AB/BA schedule, budgets, stop policy, and
   custody location.
7. After separate custodian approval, append `request-reveal`, then accept a reveal only if it
   matches the frozen commitment exactly. This does not authorize a Runtime Attempt.
8. Construct and offline-validate the exact `ControlledRunPlanV2` and `ComparisonPlanV1`.
9. Confirm the Comparison Primary is `scope="split", split_id="held-out"`.
10. Generate the `start-execution` directive containing the fresh acceptance ID and the complete
    two plans, but do not submit it until the separate live authorization is recorded.

There is no standalone `accept` action. The only legal acceptance transition atomically binds the
fresh acceptance ID, complete RunPlan, ComparisonPlan, Study projection, schedule, execution nonce,
and shared Repeat Runner identity through `start-execution`.

Record the frozen values:

| Identity or bound | Value |
|---|---|
| `study_id` | TBD |
| `intent_id` | TBD |
| `protocol_id` | TBD |
| `implementation_lock_id` | TBD |
| held-out `reveal_id` | TBD |
| fresh `acceptance_id` | TBD |
| `run_plan_id` | TBD |
| `comparison_plan_id` | TBD |
| planned Trials | Expected 54 for the current 9 × 2 × 3 design |
| maximum Attempts | Derived exact bound; expected 108 for the current retry policy |
| maximum wall time | Frozen exact bound |
| disk preflight/safe-stop bounds | Frozen exact bounds |

## 7. Required offline evidence before authorization

Attach or reference results for the exact implementation candidate:

- complete tests in Core and Companion;
- Ruff, formatting, and strict mypy;
- offline wheel and sdist builds;
- wheel digest reproduction for the locked Core artifact;
- archive secret scans;
- fake 9 × 2 × 3 completion with exactly 54 adapter calls;
- crash after claim and after Attempt publication;
- ambiguous active Attempt fail-closed behavior;
- operator pause and resume;
- budget/integrity diagnostic-only terminal packs;
- strict Evidence Pack reload;
- two byte-identical offline rebuilds; and
- independent review with no unresolved P0–P3 findings.

Evidence from another implementation digest is informative but does not satisfy this gate.

## 8. Separate live authorization

The authorization request must identify exactly:

- `study_id`, `protocol_id`, `implementation_lock_id`;
- `acceptance_id`, `run_plan_id`, and `comparison_plan_id`;
- Candidate and held-out commitment/reveal identities;
- maximum 54 planned Trials and maximum Attempt count;
- wall-time, disk, token, and monetary controls, including any unavailable control;
- credential and proxy sources without disclosing their values;
- custody directory;
- authorized operator and live window; and
- whether authorization covers only `start-execution`, one `step-execution`, or the complete
  bounded study.

Authorization never covers code changes, Candidate revision, held-out replacement, changed
implementation bytes, destructive cleanup, publication, push, PR, release, or a second study.
Any such change requires a new review or authorization.

## 9. Live operating procedure

Only after exact authorization:

1. Recheck the clean implementation state and all locked digests.
2. Recheck durable custody, available disk, budgets, credentials, and proxy configuration.
3. Submit the already-reviewed `start-execution` directive exactly once.
4. Reload the Study and record its bound execution identity before the first Attempt.
5. Submit one `step-execution` directive at a time using the latest `state_id`.
6. Observe ledger, Repeat Runner, process, container, and adapter state without modifying evidence.
7. Let an outstanding claim reconcile before considering any further step.
8. Never issue a second step while the prior Attempt is active or ambiguous.
9. Stop on any frozen pause/termination condition; do not increase timeout or budget in place.
10. On completion, verify the Evidence Pack and perform two offline rebuilds into new directories.

For a development-only pilot, a closed Runtime process may produce output that fails the frozen
Harbor/result authority checks. After exact container cleanup and private-value scanning succeed,
such output must close the claim as a non-retry `runtime_pre_terminal_failure` lifecycle Attempt.
It supplies no Agent observation and makes the affected Trial inconclusive. An authority failure
before process closure, a cleanup failure, a private-value scan failure, or an actual process loss
remains ambiguous and must not be converted into lifecycle evidence.

A quiet long-running Attempt is not itself a failure. Do not use a short supervisory timeout to
manufacture a pre-terminal result. Conversely, a disappeared Runtime with no verifiable terminal
artifact is ambiguous and must pause; it must never be silently retried.

## 10. Terminal handling

| Terminal state | Required artifact | Permitted interpretation |
|---|---|---|
| Paused | Diagnostic pack | Operational diagnosis; resumable only under the same authority |
| Terminated | Diagnostic pack | No confirmatory conclusion; not resumable |
| Completed | Evidence Pack | Protocol completed; analytical conclusion remains Core-owned |

For every terminal state, verify the closed manifest, authority files, complete ledger prefix,
execution evidence, privacy scan, and offline rebuild. Only Completed may carry authoritative Core
Batch and Comparison packages.

Do not publish, promote, push, tag, release, delete custody, or clean supporting containers/images
without a separate explicit authorization.

## 11. Final sign-off

All boxes must be checked before changing dossier status to `live-authorized`:

- [ ] New Candidate developed without held-out access.
- [ ] New held-out commitment and reveal custody established.
- [ ] Scientific question and non-success outcomes frozen.
- [ ] Exact implementation bytes and Runtime configuration locked.
- [ ] Durable Study prepared and strictly reloaded offline.
- [ ] Reveal matches the commitment created independently of Candidate Development.
- [ ] RunPlan and ComparisonPlan strictly validate and use held-out-only Primary scope.
- [ ] Trial, Attempt, wall-time, disk, token, and monetary bounds reviewed.
- [ ] Full offline evidence attaches to the exact locked implementation.
- [ ] Independent reviewer reports no unresolved P0–P3.
- [ ] `start-execution` directive is generated but has not been submitted.
- [ ] User issued a separate authorization naming the exact frozen identities and bounds.

If any box becomes false, invalidate the acceptance and return the dossier to `draft`.
