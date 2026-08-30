# Next Priority 4 Study Decision Proposal

Status: **proposal only — not selected, frozen, revealed, or authorized**

This document records the next decision boundary after the offline preparation bundle. It is not a
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

## Recommended study-design selection

The recommended choice, still awaiting explicit user selection, is:

| Field | Proposed value |
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

The mode, Primary scope, bounds, bootstrap resample count, confidence level, and missing-evidence
rule match the current non-binding preparation proposal. The case-clustered bootstrap method,
practical threshold, and detailed Guardrails are new proposals in this document and still require
explicit selection. The Candidate axis name `prompt-instruction` projects to ComparisonPlan
Treatment kind `prompt_instruction`. Every selected value must be frozen into a replacement
preparation before any Study authority is materialized.

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

If the user selects the proposed design, prepare a separate development-only work package in this
order:

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

The development pilot authorization must state the exact task authorities, maximum Attempts,
timeout, external provider scope, custody location, and stop conditions. It must not authorize
held-out reveal, `start-execution`, `step-execution`, or any part of the 54-Trial matrix.

## Exact stop point

Stop here. The preparation remains `awaiting-user-decisions`. No fresh Candidate exists, no
held-out commitment has been requested, no Study authority has been materialized, and no live or
provider execution is authorized.
