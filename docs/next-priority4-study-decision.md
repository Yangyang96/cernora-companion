# Next Priority 4 Study Decision Proposal

Status: **`confirmatory-effect` selected; systemic runtime publication root cause repaired
offline; live confirmation requires fresh authorization**

This document records the next decision boundary after the offline preparation bundle. The user
selected the recommended `confirmatory-effect` design on 2026-08-31. This file is not a
`StudyIntent`, `StudyProtocol`, Candidate Development record, held-out commitment, acceptance,
RunPlan, ComparisonPlan, or execution directive. No command in this document is authorized merely
because the document exists.

## Evidence inventory and stop decision

The repository contains a deterministic visible-corpus seed pilot and historical live artifacts.
Neither can support a new Candidate as-is:

- the deterministic seed pilot executes frozen baselines and verifiers; it does not record an
  authoritative Agent observation;
- historical Priority 4 live artifacts belong to the retired Candidate and previously exposed
  study material; using their outcomes to choose a new Candidate would cross the held-out boundary;
- `CandidateDevelopmentRecord` correctly requires at least one matching development/regression
  `agent-pilot` behavioral failure.

Therefore a fresh Candidate and its causal hypothesis cannot be honestly frozen offline from the
currently admissible evidence. Do not manufacture an observation or relabel a verifier failure as
an Agent failure.

## Selected study design

The selected choice is:

| Field | Selected value |
|---|---|
| Study mode | `confirmatory-effect` |
| Candidate Treatment axis | `prompt-instruction` only |
| Primary scope | fresh held-out Cases only |
| Primary metric | paired Reliable Success Rate delta |
| Practical threshold | absolute `+10` percentage points |
| Confidence method | 10,000-resample case-clustered paired bootstrap, 95% interval |
| Repetitions | `k=3`, independent Trials |
| Evaluation-validity Guardrail | no adverse change |
| Protected-path Guardrail | no adverse change |
| Regression RSR Guardrail | no more than `-10` percentage points |
| Missing or incomplete evidence | `inconclusive` |
| Proposed bounds | 9 Cases x 2 Configurations x 3 repetitions; 54 Trials; at most 108 Attempts; 43,200 seconds |

The Candidate axis name `prompt-instruction` projects to ComparisonPlan Treatment kind
`prompt_instruction`. These values select the design direction but do not freeze a Candidate or
authorize a Study. They must be frozen into a replacement preparation only after admissible
development evidence exists.

## Scientific-question template

The final question must be completed only after a legitimate development Agent failure exists:

> On a newly committed held-out synthetic Python repair set, does one frozen
> `prompt-instruction` patch, derived solely from the development/regression failure
> `[failure-code]` through the predeclared mechanism `[mechanism]`, improve paired Reliable Success
> Rate by at least 10 percentage points over the frozen Baseline while every hard Guardrail passes?

`[failure-code]` and `[mechanism]` are evidence fields, not authoring placeholders that may be
filled from intuition. Until a matching authoritative Agent observation exists, the scientific
question remains pending.

## Next bounded work package

The selected design uses this separate development-only work package in order:

1. Create and review a fresh development/regression corpus without held-out material.
2. Freeze Baseline, Runtime, Harness, model, reasoning, tool schema, timeout, retry, and resource
   limits for the development pilot.
3. Request separate authorization for only the bounded development Agent pilot. This is not a
   smoke and is not the 54-Trial study.
4. Evaluate all pilot Attempts with strict receipts and classify the leading failure without
   qualitative held-out input.
5. If no valid behavioral failure appears, stop with `no-candidate`; do not invent a treatment.
6. Otherwise write one causal hypothesis, apply one `prompt-instruction` patch, and materialize the
   fresh `CandidateDevelopmentRecord`.
7. Obtain an independent review of the Candidate and development evidence.
8. Only then ask an independent custodian for a fresh opaque held-out commitment.

Steps 1 through 3 have been repeated under a repaired Plan v3 authority. Step 4 is pending explicit
authorization for the new request below. The two earlier claimed Attempts remain permanently
ambiguous and are not reused. There is no authoritative Agent observation, failure mechanism,
Candidate, Candidate Development record, held-out commitment, reveal, smoke, or Study execution.

The development pilot authorization must state the exact task authorities, maximum Attempts,
timeout, external provider scope, custody location, and stop conditions. It must not authorize
held-out reveal, `start-execution`, `step-execution`, or any part of the 54-Trial matrix.

## Historical first development-pilot request

The first closed request bundle was
[`preparations/next-priority4-development-pilot`](../preparations/next-priority4-development-pilot).
Its exact identities and implementation candidates are:

| Authority | Value |
|---|---|
| Bundle | `80cbc4aac215d16b7e3f4adcdb5f276250ac064bdff64afdb8553e629bb8d51c` |
| Authorization request | `0ae0c5a7f65a0444decdb32d25ac99a1851f710dd87c08cf3e2898a058cea1ad` |
| Development pilot plan | `273b259f2f0528dbccee2808c5e42e73173841b2b313f13bf84c467369710c43` |
| Corpus | `dd6fa131ddcf717fb589e6398e4916998ab3d53bd297cfce3c08529ce46adece` |
| Image set | `ec601643b2f92edb907997d4fd742421d9527f2932817156f73ef62a7c5a448e` |
| Cernora Core wheel | `4ef10a5eb2f9961943883576ab81bc97ce32d2f3f8a88cb9679d5c51c81e368d` |
| Companion wheel | `c4f24d6e5443fdc67b1232cfb11855c0ec5d9aced6fd502140d15a1c46e74c6f` |
| Prepared execution | `46004fc1f812cbef2edfc5534a5a6e133a35ef85b05c9c4e5420722c5b8cd2a8` |

Both wheel candidates were built twice offline with byte-identical SHA-256 values. Each of the six
task images was built twice with network disabled and matched its recorded immutable digest. The
prepared custody has zero Attempts and zero completed Trials; its ledger contains no
`execution-started` event. Calibration only establishes that each frozen baseline fails and each
fixture solution passes its verifier. Every calibration says `source=verifier-calibration` and
`agent_outcome=not-observed`; none is a real Agent failure.

Authorization would cover exactly six baseline-only Trials, at most twelve Attempts, concurrency
one, 300 seconds per Attempt, and 7,200 seconds total wall time. Provider scope is authenticated
OpenAI Codex generation only. Custody is the git-ignored directory
`.agent/custody/development-pilot-273b259f2f0528dbccee2808c5e42e73173841b2b313f13bf84c467369710c43`.
The pilot must stop as `no-candidate` if all six Agent outcomes pass, `inconclusive` for missing or
incomplete evidence, or `candidate-eligible` before Candidate construction when a matching real
behavioral failure exists.

The user authorized request
`0ae0c5a7f65a0444decdb32d25ac99a1851f710dd87c08cf3e2898a058cea1ad`. The first
Attempt durably recorded `execution-started` and `attempt-claimed`, then the closed Harbor process
failed strict result processing before publishing an Attempt artifact. No later Trial was started.
The outer command actually returned exit code 1 after 304.913924 seconds; loss of the observer
handle was not evidence that the host process disappeared. The then-active private-value scan bug
and equal inner/outer timeout were both present, but the surviving custody cannot prove which
prepublication boundary was reached. The claim therefore remains ambiguous and permanently
non-retryable. This is not an Agent failure and does not authorize reuse of the request or custody.

## Recovery development-pilot preparation

The replacement closed request bundle is
[`preparations/next-priority4-development-pilot-recovery`](../preparations/next-priority4-development-pilot-recovery).
It retains the same fresh six-Case corpus and exact image set, but binds the reviewed implementation
wheel identities directly into Plan v2. Historical Plan v1 remains inspectable but cannot be used
to prepare or step a new execution.

| Authority | Value |
|---|---|
| Bundle | `a826de5070aed79a143081cf3b9d22b69190399c88e8addec4f792c3df686cba` |
| Authorization request | `df60c04812b9e1755848fe0c61face5d45bfa1294dd3b874023e1caa12f585fd` |
| Development pilot plan | `3d5a11931a3aa9ddfcf8ab3a9052dd17d43512726e68829a89429019b54ca223` |
| Prepared execution | `c655a8db6dc3fa5014049f5fcafd57d3db0f7f4546e077234fc649925e67be96` |
| Corpus | `dd6fa131ddcf717fb589e6398e4916998ab3d53bd297cfce3c08529ce46adece` |
| Image set | `ec601643b2f92edb907997d4fd742421d9527f2932817156f73ef62a7c5a448e` |
| Cernora Core wheel | `4ef10a5eb2f9961943883576ab81bc97ce32d2f3f8a88cb9679d5c51c81e368d` |
| Companion wheel | `cdf8cf1c6e245c6bf9c3b8536d438797aa0504ed4bdef4c24b5400b2b2fce411` |

The recovery implementation closes a post-process authority failure only after process closure,
container cleanup, and private-value scanning succeed. It publishes a non-retry lifecycle Attempt
with no Runtime observation, repair result, or evaluation, so the affected Trial is inconclusive.
Private-value failures, cleanup failures, and real process loss remain fail-closed and ambiguous.
The lifecycle policy is explicitly enabled only by the development-pilot entry point. Before any
claim, that entry point verifies the active repository venv and every installed wheel member
against the exact Core and Companion candidates bound by Plan v2. The replacement Companion wheel
was built twice offline with byte-identical SHA-256 values.

## Independent offline review and validation

Independent read-only and adversarial review examined the implementation from `d3109df` through
the final working-tree snapshot on separate Standards, Spec, runtime, custody, and authorization
axes. Findings covering strict trees, orphan artifacts, spawn-window cleanup, duplicate custody,
self-consistent custody rewriting, and contradictory request authorities were fixed and
re-reviewed. The final review reports no unresolved P0-P3.

The final offline publication gate passed 572 tests, Ruff, format checking, strict Mypy across 143
source files, byte-identical dual wheel builds, wheel and sdist builds, license inventory,
repository secret scanning, and built-artifact secret scanning. No validation step used Runtime
credentials or submitted an Agent/provider Attempt.

## Exact recovery stop point

The user authorized the complete development-only pilot for request
`df60c04812b9e1755848fe0c61face5d45bfa1294dd3b874023e1caa12f585fd`. Execution
`c655a8db6dc3fa5014049f5fcafd57d3db0f7f4546e077234fc649925e67be96` durably recorded
`execution-started` and one `attempt-claimed` event for ordinal 1, slot 1. The outer command then
returned exit code 1 after 302.364721 seconds with only the value-free generic error. Reproduction
showed that the entry point passed all ambient environment values into the private-value scanner;
ordinary values such as `1`, `2`, and `dumb` were consequently treated as proxy secrets, so normal
Harbor output triggered a false private-value failure before the lifecycle-closing catch. The
temporary artifact tree was correctly removed by that fail-closed path, leaving no publication.
Offline custody replay reports `status=running`, zero published Attempts, zero completed Trials,
and no outcome.

Stop here with an `inconclusive` pilot result. The outstanding claim is permanently ambiguous and
must not be retried, resumed, converted into lifecycle evidence, or used as an Agent observation.
No later Trial was started. No fresh Candidate exists, no held-out commitment has been requested,
and no Study authority has been materialized. This authorization did not cover held-out access or
reveal, smoke execution, or any Trial in the future 54-Trial matrix.

## Repaired development-pilot preparation

The new closed request bundle is
[`preparations/next-priority4-development-pilot-repair`](../preparations/next-priority4-development-pilot-repair).
Plan v3 scans only the three proxy endpoints actually selected by policy, gives the 300-second
Agent limit a separately frozen 360-second Attempt envelope, terminates the complete process group
on interrupts, writes value-free incident receipts, and adopts only an exact already-published
orphan artifact without another provider call. The canonical authorization request is stored in
custody and replay binds its exact ID, Plan, ordered Case authorities, envelope, and physical
custody path before any claim. Plans v1 and v2 remain inspectable but cannot start new execution.

| Authority | Value |
|---|---|
| Bundle | `197d4bf9cf25b902a0706d978f242c5d5f325e9659ff5934ad1fbcd9abcd77d4` |
| Authorization request | `d34dfab521aeadb749d06f8d37b3df27a6eca4d0977374f88e27f39d65ed1c26` |
| Development pilot plan | `33544aa6d8ec292daf8b69390c4731ee67ee70184f2e7cd788c3baeeb4824099` |
| Prepared execution | `bb5e9c7b87650a788c0eca899329110fd25c2ef6421ec4865c894cb5e9945ebf` |
| Physical custody path | `7ee5eb85c6c136af2169805efdb5166a1d07816d2336da54d90078c0446430fb` |
| Cernora Core wheel | `4ef10a5eb2f9961943883576ab81bc97ce32d2f3f8a88cb9679d5c51c81e368d` |
| Companion wheel | `7288579a4676fbcd2f4c3e31a0f4f6c34979184a70c90987a2e031606bbc151b` |

The user authorized the complete development-only pilot for request
`d34dfab521aeadb749d06f8d37b3df27a6eca4d0977374f88e27f39d65ed1c26`. Execution completed all
six Trials with exactly six Attempts and outcome
`f381df0db6cdcbea00d6bd8f850100a5b519a171f92d6c38757dc91d7a56a08b`, status `inconclusive`.
Each Attempt published an exact non-retry lifecycle artifact with
`runtime-pre-terminal-failure`, no Runtime observation, no repair result, and no evaluation. The
individual recorded durations were 316,149 through 319,974 milliseconds; the full execution used
2,130,235 milliseconds, within the frozen 7,200-second wall bound. The ledger contains one
`execution-started`, six claim/publication pairs, and one `completed` event. Offline replay finds
no ambiguous claim, orphan artifact, or incident receipt.

These lifecycle records are not Agent failures or behavioral observations. No Candidate is
eligible, and no Candidate construction, smoke, held-out access or reveal, Controlled Study
execution, or 54-Trial work is authorized or has started. Stop at the completed `inconclusive`
development-pilot outcome.

Post-completion replay, 121 focused offline tests, Ruff, format checking, strict Mypy, repository
and custody secret scans, and an exact credential-value absence scan all passed. Independent
read-only review recomputed the ledger, artifact, request, Plan, execution, outcome, and physical
path bindings and found no unresolved P0-P3. The reviewed custody-tree snapshot digest is
`8caac73411d024dde246914b2654c7cddd7785044c89f982a95477702efe2ff9`.

## Runtime pre-terminal root-cause diagnosis

Offline analysis identified a classifier-order defect that can collapse a normal Harbor
`AgentTimeoutError` into `runtime_pre_terminal_failure`: Harbor initializes `agent_result` before
the Agent phase, while the companion previously treated any present Agent result as generic
pre-terminal failure before examining the exception type. An exact closed-result replay went red
on that behavior. The implementation now accepts `timed_out` only when the strict Harbor result
contains `AgentTimeoutError`, a strictly typed Codex AgentContext (empty or populated from partial
session metrics), complete ordered Agent/exception timing, and the enabled Verifier's exact reward
result and ordered timing; contradictory timeout evidence fails closed.

The six retained Attempts remain `inconclusive` because their safe published artifacts do not
contain the raw exception discriminator. Their 316,149–319,974 millisecond durations make the
timeout mechanism consistent with all six Cases but cannot retroactively establish it as their
explanation.
The evidence, qualification, fix, and proposed one-Case/one-Attempt confirmation boundary are
recorded in
[`runtime-pre-terminal-diagnosis.md`](runtime-pre-terminal-diagnosis.md).

The exact one-shot diagnostic control plane is separate from the closed six-Case authority. The
user authorized Plan `6a342640911cade0ed3bd381e3ff80e0327d5230817a72ef6bac5d46e8d8bd4a` and
request `5b6bb9b88e8923cac40a5924597dbe5d9f9faa879731d307edfb8013034fba42`; its sole Attempt
published complete non-retry terminal evidence but remained `runtime-pre-terminal-failure`, so
the Agent-timeout hypothesis was not confirmed. That authority is consumed and cannot be retried.

Three later one-shot discriminators successively proved and repaired the JobConfig gate, the
TrialConfig gate, and the final post-timeout evidence defect. The latest consumed Attempt passed
both configuration gates and recorded `agent-timeout-verifier-result`, thereby confirming the
live `AgentTimeoutError` path. The classifier had incorrectly required the post-timeout Verifier
to produce a reward before retaining that terminal timeout. The offline repair now accepts a
missing Verifier result only as non-evaluated `timed_out` evidence when the independent exception,
Agent timing, Verifier timing, message, traceback, and duration constraints all agree. No
Candidate, smoke, held-out, or Study evidence results from this repair; one fresh development-only
confirmation remains the next authorization boundary. Its closed proposal binds Companion wheel
`ea0f48a6a0db3e539b7b2b55942c72d677aabc8f9984cf57068750958b4dee5e`, Plan
`a31414dfcaef392ffe33a648f715aff9d2ce55812ee066064fcbe646759adcfe`, and request
`e368f68cecceb854d2947149ab6c7ad4e230c45549d9aef00fa92b42981b742a`;
at materialization it was not prepared, claimed, or authorized. The user subsequently authorized
it. Its sole Attempt closed as non-retry `timed_out` after 315,506 milliseconds with diagnostic
code `agent-timeout-evidence-accepted`, live-confirming the pre-terminal publication repair. It
produced no behavioral or Candidate evidence, and the authority is fully consumed.

Offline repair now persists only a fixed value-free discriminator before artifact publication and
binds it into the diagnostic outcome. It never retains raw Harbor output, exception text,
credentials, proxies, or transcripts. A fresh one-Case/one-Attempt proposal requires new wheel,
Plan, and request identities after full gates and independent review.

The follow-up closed proposal was
[`preparations/next-priority4-runtime-diagnostic-followup`](../preparations/next-priority4-runtime-diagnostic-followup),
with Companion wheel SHA-256
`56db15a084ffd2132f083a000cc0cf882661c5f85e6ed155a9e48af59475557e`, Plan
`b039fa42eafc1a85be6e79bbbb4952639f64d8b4b89d3f62184b68838058ff76`, and request
`8f9243aef0b8d03cc2733a9a57ab7195699469d806e3d3d0cf9a046c47016149`. The user exactly
authorized it, and its sole Attempt durably emitted `job-config-authority-rejected` before closing
inconclusively. That authority is now fully consumed.

Running the executor's exact argv through Harbor 0.16.1's real parser offline proved the systemic
publication root cause: the validator confused unresolved Job-level task/agent configuration with
normalized Trial-level configuration and fixed-order compared a set-backed retry field. The
validator repair now passes the real parser while preserving all other exact field/type checks.
A fresh one-Case/one-Attempt proposal bound to the repaired wheel is the next stop point. It is not
a retry or resume of any closed request, cannot produce Candidate Development evidence by itself,
and authorizes no smoke, held-out, reveal, Study, or 54-Trial action.

The closed confirmation proposal is
[`preparations/next-priority4-runtime-fix-confirmation`](../preparations/next-priority4-runtime-fix-confirmation),
with Companion wheel SHA-256
`9bfe8711049241a368e6259c9b68fa2759e94b297fa140f2e11edc98d28081c7`, Plan
`45fc67a38cb662fea6bab5a5deead3049b93005c18215e0e8c5dec725e991e0b`, and request
`6ce143b2774fd21ece87c4144a12db81b0147f2de5d4b1ce16d6c32366f27f75`.
At materialization it was unprepared, unclaimed, and explicitly unauthorized; work stopped before
accepting those identities.

The user subsequently authorized and consumed that exact one-shot authority. It advanced beyond
the repaired JobConfig gate, then durably closed with `preterminal-structure-rejected` after
318,513 milliseconds. Offline construction of Harbor's real TrialConfig proved a second layer of
the same harness defect: Harbor preserves the unresolved custom Agent representation from Job to
Trial, while the validator and fake fixture expected a normalized representation. This necessarily
rejected the valid Trial result before timeout classification. The Trial validator and fixture are
now repaired and the mismatch has its own fixed value-free code.

The replacement closed proposal is
[`preparations/next-priority4-runtime-trial-config-confirmation`](../preparations/next-priority4-runtime-trial-config-confirmation),
with Companion wheel SHA-256
`6587da0fd9841a7e9815ec136db3a60e6329c375f6f12645dea7812faa0132c8`, Plan
`af27b05cbc54b6649856bac996106c21794de3f769bb75f9b98d50dfed87f2bd`, and request
`18ecdf5e3778dbef994fa9733e17cf012bcc82e71c0bd5fef0681dfb07e3763a`.
At materialization it was unprepared, unclaimed, and unauthorized.


## Pi-era development-only pilot preparation (2026-09-04)

After the runtime switch, the development-line corpus and control plane were migrated to the pinned
pi Runtime for the next bounded development-only Agent pilot. The visible corpus is extended from
six to nine fresh Cases (six `development`, three `regression`) with three new Case authorities
(`p4-dev-csv-quoted`, `p4-dev-range-intersect`, `p4-dev-slug-collapse`). The pilot contract family
moves to Plan `v4`, corpus/images `v2`, authorization request `v3`, and execution record `v3`:
nine Trials, at most eighteen Attempts, a 600-second Agent timeout inside a 660-second Attempt
envelope, a 14,400-second wall bound, concurrency one, and provider scope
`pi-authenticated-generation-only` with authentication from `PI_AUTH_JSON_PATH`. Historical
v1-v3 contracts remain loadable only through frozen legacy constants and version-pairing maps;
the current seams (`prepare`, `step`, runtime verification) require Plan v4 and request v3. A
focused independent review of the migration found no P0/P1; two P2 and three P3 findings were
repaired offline and re-verified.

The new closed request bundle is
[`preparations/next-priority4-development-pilot-pi`](../preparations/next-priority4-development-pilot-pi),
bound to the pinned pi base image and image set
`285c1712852b870c9ddfeb8954b49e8aa51a4d8065eb3570f6f1667229bbf798`:

| Authority | Value |
|---|---|
| Bundle | `c34f5b656f11ba900b311e0ba29a8ce734c3045bebfde7e38c08d6fb7f18583f` |
| Plan | `1e22ec497f0535282625c646fe5cde8aa9c2c5dc3378a1fba4abac7d29e44b7e` |
| Authorization request | `3df49127b1acc716e41524a2289d8934ed4014f449b57b6241798af1f7df5cc0` |
| Corpus | `89cd20fd80630448524a6d4c722f54d52139e3d29f87314faef10b04013ce0cd` |
| Image set | `285c1712852b870c9ddfeb8954b49e8aa51a4d8065eb3570f6f1667229bbf798` |
| Cernora Core wheel | `0.1.4`, SHA-256 `4ef10a5eb2f9961943883576ab81bc97ce32d2f3f8a88cb9679d5c51c81e368d` |
| Companion wheel | `0.4.0`, SHA-256 `3806453f75f29c2537e0f1485c23aad738dabc761df714d56cfeb23fa0fb3b12` |

The Companion wheel was built twice offline with byte-identical digests, all nine task images were
built twice per the existing no-cache reproducibility procedure, and the bundle was strictly
verified against both exact wheel artifacts. Its status remains
`awaiting-development-pilot-authorization`: creation grants no execution authority. Authorization,
if granted, authors only the nine-Trial baseline development pilot above and stops before
Candidate construction.


## Live development pilot campaign (2026-09-04)

Five pi-era development pilots ran under exact fresh authorities. The campaign
repaired four live-only defects that offline gates could not expose, and it
froze two authorities on the ambiguity protocol instead of producing a
candidate-eligible outcome:

1. **r1** (plan 1e22ec49, wheel 48f87698-predecessor) — every attempt closed
   pre-terminal. Root cause: the trial-result validator expected a Codex-era
   unsplit `model_info`, while the pinned pi Runtime reports the provider
   split. Repaired in 7fd8f3a.
2. **r2** (plan 2cbb430e, wheel 48f87698) — three clean timeouts and six
   pre-terminal closures. Root cause: Harbor's docker compose bind mounts
   silently lose container-side writes when the evaluation tree lives under
   macOS system temporary directories, which the pi session-directory contract
   then reports as missing. Repaired by pinning the pilot evaluation tree to
   an operator-owned VM-shared root, and the Agent timeout moved from 600 to
   1,800 seconds across v4/v5/v6 plan generations as three corpus Cases were
   observed closing at each shorter ceiling (a4c2122, 0f15262, 7c3186b).
3. **r3** (plan 42d81add, wheel d7684a26) — two evaluated Trials (csv and
   json-pointer behavioral failures) then a slot-3 ambiguous claim: a
   controlled-attempt-error raised by the executor after the claim without a
   terminal publication. The execution froze under the ambiguity protocol.
4. **r4** (plan 2026f510, wheel d7684a26) — six evaluated Trials including
   **five authoritative behavioral failures** and one pass, three timeout
   Trials, before the outcome derivation rejected attempts that also carried
   `unauthorized_path_changed_v1` next to the declared code. Repaired to a
   membership check in 7c3186b.
5. **r5** (plan 8801699d, wheel 87bff95d) — a slot-1 controlled-attempt-error
   after a 37-minute claim froze the execution under the ambiguity protocol.

The r4 custody keeps the five behavioral-failure attempt artifacts as frozen
evidence; per the stop policy its execution is inconclusive and cannot mint
development observations. The remaining blocker is the executor-side
controlled-attempt-error family after an active claim — value-free by
contract, so diagnosing it requires the one-shot runtime-diagnostic authority
cycle (a fresh wheel, diagnostic plan, request, and authorization per the
established pattern), not further pilot rounds. All five bundles are archived
under `preparations/next-priority4-development-pilot-pi-r*` with their frozen
custodies; no Candidate, held-out, smoke, Study, or 54-Trial authority was
consumed. The sixth pilot must not reuse any retired plan.


## Executor-phase controlled-attempt diagnostic preparation (2026-09-06)

The r3 and r5 incident receipts recorded only `phase=executor` and the broad
`controlled-attempt-error` category, so the frozen custodies retain no
discriminator for the closing exception. Offline review bounded the family
exactly: a `ContractError` escaping `ControlledHarborAttemptExecutor.__call__`
outside the strict-result conversion region — the pre-process
authority/auth/argv/image checks, the post-process docker cleanup inside the
process `finally`, and the private-value scan. The timing of the two freezes
(≈118 s in, and ≈37.5 min in, i.e. ≈390 s past the 1,860 s envelope kill)
places both inside the post-process region.

The instrumented repair closes that observability gap while keeping receipts
value-free:

- every unconverted `LiveAttemptError` raise site now carries one fixed
  kebab-case `diagnostic_code` (auth-file-*, docker-*, image-*, private-value-*,
  harbor-argv-*, scan-tree-unreadable, …), and the private-value scan wraps an
  unreadable closed Harbor tree as `scan-tree-unreadable` instead of letting a
  plain file-size or symlink `ContractError` escape unclassified;
- the executor retains the code of a closing contract error
  (`unclassified-contract-error` fallback) without any raw evidence;
- development-pilot incident receipts move to `/v2` with a pattern-locked
  `discriminator` field; legacy `/v1` receipts remain loadable, and the frozen
  r3/r5 incident receipts themselves replay byte-exactly (full r1–r4 custody
  replay was already blocked by the era-boundary contract drift before this
  work);
- the one-shot runtime-diagnostic control plane persists a value-free
  diagnostic receipt before freezing on the executor-exception path, and binds
  the same code into the diagnostic outcome when the Attempt publishes;
- the diagnostic source pin moved from the never-live synthetic Plan
  `458777b90…` to the frozen r5 development Plan
  `8801699dbda55bab8b3edfdc9ec190c62dac667419c93b35465c899a35882771`, binding
  the real pi-runtime task images so the diagnostic Attempt can verify its
  image live instead of failing on a synthetic digest.

Offline gates after the change: strict Mypy across 152 source files, Ruff
check and format, and the full test suite. An independent adversarial review
of the instrumentation found no P0; its P1 (an operator interrupt could be
recorded as `unclassified-executor-exception` in the diagnostic plane) and two
P2 findings (cross-plane fallback-code alignment and this document's replay
claim) were fixed before the proposal was regenerated. The public live step
also now pins its evaluation tree to an operator-owned VM-shared
`~/.cernora/runtime-diagnostic-evaluation` root, because macOS system temporary
directories silently lose compose bind-mount writes (the r2-era root cause).

The c7d8ee3c authority — Plan
`c7d8ee3c74d11d352b9b0bbcd9457a1ab8fdbed9c95a0987321f50e5ade6b421`, request
`8e13ea9af0ce826dae7357dc6f10b50150181d692b7f77c62ca4a52fd8fb1eaa`, with
Companion wheel
`ce9409e45c67329147ea9833060476ce76d97dc3998ca791cc19ad27a18ad8b9` (built twice
byte-identical) — was executed under the session's standing diagnostic mandate
(the 2026-09-06 instruction to walk the one-shot diagnostic cycle) after the
exact Plan/request identities were published in the session and two explicit
authorization prompts timed out unanswered. Its execution is recorded in the
next section.


## First live executor-diagnostic consumption (2026-09-07)

Execution `98ac9a4971c43d5dc05b3b1e06749ea03b007f5b7457001b5e05a196226d10b`
under Plan `c7d8ee3c…` claimed its only Attempt at
1788710656273 and froze 100.8 seconds later with the incident
`phase=attempt-validation`, category `ambiguous-one-shot-attempt`. The executor
succeeded — the pi agent completed the json-pointer repair inside the normal
88–270 s window, the container cleanup and private-value scan closed, and a
complete evaluated attempt was returned — and the executor-side
controlled-attempt-error family from r3/r5 did **not** reproduce. Instead, the
attempt-validation boundary rejected the returned attempt.

Offline replay proved the root cause: the diagnostic specification was
assembled by copying the nine-Case source Plan's suite-derived
sub-authorities, so `expected_evaluation_authority` embedded the nine-Case
profile identity (`496913f2…`), while the live one-task evaluation builds its
package under `ControlledRepairProfile((json-pointer,))` (`1c10d19e…`). No
single-task Evaluation Package can ever match a nine-Case-bound specification;
`verify_authority` therefore fails closed for any fully evaluated diagnostic
attempt. The same latent construction defect existed since the codex-era
one-shots but never fired, because every earlier diagnostic Attempt closed
pre-terminal and never produced an Evaluation Package.

The repair rebuilds the diagnostic specification through the canonical
`build_controlled_specifications` machinery with the single-task suite, the
pinned baseline prompt source, the frozen bootstrap policy, the real pi-runtime
image, and the diagnostic 300-second timeout, so the executed environment and
the specification bind the same authorities. An offline replay of the exact
evaluation (r4's frozen json-pointer repair result under the one-task suite)
now equals the regenerated specification's expected evaluation authority, and a
regression test pins that single-case consistency while asserting the
nine-Case profile cannot satisfy it. Offline gates after the repair: strict
Mypy across 152 files, Ruff check and format, 136 focused tests, and the full
suite run of record.

The replacement closed proposal is
[`preparations/next-priority4-runtime-executor-diagnostic`](../preparations/next-priority4-runtime-executor-diagnostic):

| Authority | Value |
|---|---|
| Plan | `4fba5a12c0028dbdc47294e59a19d7c2a184ec3adad85a636a66d60af700c7ba` |
| Authorization request | `8d3403c31502585c5407306722918b0764fa2121a3db75138cab4fff40c94cae` |
| Source development plan | `8801699dbda55bab8b3edfdc9ec190c62dac667419c93b35465c899a35882771` (frozen r5, read-only) |
| Case | `p4-dev-json-pointer`, real image `95c21efa…ae7959d`, single-case authorities |
| Cernora Core wheel | `0.1.4`, SHA-256 `4ef10a5eb2f9961943883576ab81bc97ce32d2f3f8a88cb9679d5c51c81e368d` |
| Companion wheel | `0.4.0`, SHA-256 `ea4a6aa956cd25d6f1da107488ef14caf6353e6189998d1827c68d5661ed91e4` (built twice byte-identical) |

Authorization, if granted, covers exactly one development-only Trial, one
Attempt, no retry, concurrency one, a 300-second Agent timeout inside a
360-second Attempt envelope and a 900-second wall bound, provider scope
`pi-authenticated-generation-only`, and one diagnostic-only controlled terminal
artifact. A frozen claim still persists its fixed value-free family code in the
diagnostic receipt. It authorizes no Candidate construction, held-out access or
reveal, smoke execution, Study execution, or 54-Trial work, and at
materialization it is unprepared and unauthorized.

