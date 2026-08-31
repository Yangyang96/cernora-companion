# Next Priority 4 Study Decision Proposal

Status: **`confirmatory-effect` selected; development pilot offline-prepared, not authorized**

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

Steps 1 and 2 are complete. Step 3 is the current boundary. Steps 4 through 8 have not begun.
There is no authoritative Agent observation, failure mechanism, Candidate, Candidate Development
record, held-out commitment, reveal, smoke, or Study execution.

The development pilot authorization must state the exact task authorities, maximum Attempts,
timeout, external provider scope, custody location, and stop conditions. It must not authorize
held-out reveal, `start-execution`, `step-execution`, or any part of the 54-Trial matrix.

## Offline development-pilot preparation

The closed request bundle is
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

## Exact stop point

Stop here. The bundle remains `awaiting-development-pilot-authorization`, and the prepared custody
remains `prepared`. No fresh Candidate exists, no held-out commitment has been requested, no Study
authority has been materialized, and no Agent or provider execution is authorized.
