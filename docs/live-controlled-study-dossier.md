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

### Selected development-only pilot boundary

The user selected `confirmatory-effect` on 2026-08-31. The separate closed development-only bundle
is [`preparations/next-priority4-development-pilot`](../preparations/next-priority4-development-pilot),
with bundle ID
`80cbc4aac215d16b7e3f4adcdb5f276250ac064bdff64afdb8553e629bb8d51c`, plan ID
`273b259f2f0528dbccee2808c5e42e73173841b2b313f13bf84c467369710c43`, and authorization-request
ID `0ae0c5a7f65a0444decdb32d25ac99a1851f710dd87c08cf3e2898a058cea1ad`.
Its six fresh visible Cases are three `development` and three `regression` Cases; it contains no
held-out material. The custody ledger is prepared offline with execution ID
`46004fc1f812cbef2edfc5534a5a6e133a35ef85b05c9c4e5420722c5b8cd2a8`, zero Attempts, and no
`execution-started` event.

This boundary is not the dossier's live authorization. It permits neither held-out access or
reveal, smoke execution, `start-execution`, `step-execution`, nor any Trial in the future 54-Trial
matrix. A separate explicit authorization may cover only the six baseline Agent pilot Trials and
their frozen maximum of twelve Attempts. Candidate construction remains impossible until that
pilot produces at least one matching authoritative behavioral failure.

Independent read-only Standards and Spec review tasks examined the implementation from `d3109df`
through `072ce59`. Two strict-tree findings were fixed and adversarially re-reviewed; both axes
report no unresolved P0-P3. The exact post-fix implementation passed 533 offline tests, Ruff,
format checking, strict Mypy, reproducible wheel/sdist builds, and repository/archive secret scans.
This review satisfies only the development-pilot authorization boundary; it is not Candidate,
held-out, reveal, smoke, or live-study review.

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
