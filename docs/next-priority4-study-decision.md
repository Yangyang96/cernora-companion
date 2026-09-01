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
