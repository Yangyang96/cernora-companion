# Next Priority 4 Study Decision Proposal

Status: **`confirmatory-effect` selected; repaired development pilot awaiting authorization**

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
