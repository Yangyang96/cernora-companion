# P4 Candidate Development — csv_quoted_field_scanning_v1

Status: **frozen development candidate; canonical StudyIntent embedding happens at study
materialization**

Development record: `record.json`, `development_id`
`1b0a62491a8f6e7c1cc85ce300ad3a9fd12940376541581bbb6b7f6fe1d69fcc`
(schema `cernora.reference.candidate-development/v1`).

This worksheet freezes the development-line Candidate content derived solely from the
r9 development-pilot evidence (outcome `c6427fab…`, status `candidate-eligible`). It is
not a `StudyIntent`, held-out commitment, reveal, smoke, or execution directive.

## Evidence basis

- r9 execution `b3cae298…` under Plan `d81076fa…`, request `0ac96b0e…`: nine Trials,
  nine Attempts, zero incidents, zero freezes; the campaign's first candidate-eligible
  outcome with leading failure code `csv_quoted_field_scanning_v1`.
- The embedded observations are byte-copied from the r9 outcome's nine Development
  Observations (seven authoritative behavioral failures, two passes); each
  `evidence_sha256` is the digest of the published controlled Attempt.
- The csv Case itself: the r9 slot-1 Attempt changed only `src/csv_field.py`, left
  `tests/verify.py` untouched, ran the verifier once (exit 1), and terminated cleanly
  in 237.8 s — a pure knowledge failure of the quoted-field scanning semantics. The
  same Case failed behaviorally in every consumed round that evaluated it (r3, r4,
  r6, r8, r9).

## Treatment (axis: prompt-instruction)

Baseline prompt authority (r9-frozen, unchanged): `p4-confirmatory-baseline-prompt-v1`
payload `{"text": "Repair the task from its declared behavior and the available
workspace evidence."}`, source SHA-256 `acf0b631678cdc9b3c87e7c2d845b3cc28f84b316cf1e2de6d6954adfb856eb5`.

Candidate prompt authority: `p4-confirmatory-candidate-prompt-v1`, payload
`{"selected_failure": {"code": "csv_quoted_field_scanning_v1", "profile_id":
"cernora-controlled-repair-v1", "profile_version": "1.0.0"}, "text": <treatment text>}`,
source SHA-256 (equals the record's `treatment_sha256`)
`118ae58150209154c33853f868321b48fa3d79e98eb26cf7e6149ff65b743d1a`. The canonical
authority bytes are committed alongside this worksheet as `candidate-prompt.json`
(its `source_sha256` must equal `118ae581…`); it is the byte-level input the study
re-mint must consume, not this document's renderings.

Treatment text (verbatim, additive single-variable design: the baseline sentence plus
one exact-semantics sentence):

> Repair the task from its declared behavior and the available workspace evidence.
> When the target scans delimited text with quoted fields, follow RFC 4180 exactly:
> honor a quote only at a field boundary, turn a doubled quote into one quote
> character, keep embedded delimiters and newlines inside quoted fields, yield an
> empty field for consecutive or trailing delimiters (an entirely empty line is one
> empty field), and reject an unterminated quoted field with an error.

Everything else stays byte-identical to the r9 Baseline: the configuration-level
instruction (`treatment-instruction`, "Change only allowed source files."), runtime,
model, reasoning effort, tool schema, generation configuration, limits (1,800 s agent
timeout / 1,860 s envelope), retry policy, per-Case prompts, and task images.

## Development-line authority digests

The record's `baseline`/`candidate` `authority_sha256` values are the configuration
authorities derived from the nine-Case visible corpus with the r9 specification knobs
(reconstruction validated: the rebuilt baseline specifications are byte-identical to
the frozen r9 Plan's nine experiment specifications):

| Authority | Value |
|---|---|
| Baseline configuration (development derivation) | `6a4c06e84e6336318f4f0f043ab6735f4e18008d1bb934c1257d04bad57be944` |
| Candidate configuration (development derivation) | `2b2fa3b92e5fff75e614caa124b19ac249dd75cbd37c50c9db32d34e6f717442` |
| Treatment payload digest (case-set independent) | `118ae58150209154c33853f868321b48fa3d79e98eb26cf7e6149ff65b743d1a` |

## Sequencing note for study materialization

`bind_study_run_plan` compares a `CandidateDevelopmentRecord`'s configuration
authorities against the final study `ControlledRunPlanV2`, whose core projections
include the full study dataset digest — the study Case set (nine observed Cases plus
three fresh held-out Cases) does not exist yet. Therefore this worksheet freezes the
**content** now (hypothesis, treatment payload with its case-set-independent digest,
and the nine observations); at study materialization the canonical record embedded in
the `StudyIntent` must be re-minted from the same byte-identical prompt payloads over
the final Case set, yielding new configuration-authority digests while the
`treatment_sha256` stays `118ae581…`. The prompt-payload bytes frozen here predate any
held-out access and prove the patch was chosen without held-out knowledge.

Because the configuration digests are content of the record, the re-minted canonical
record necessarily carries a **different `development_id`** from
`1b0a6249…`; only this development record and the treatment payload digest are
terminal. Continuity between this freeze and the re-mint is enforced mechanically:
the materialization must assert
`candidate_continuity_violations(prior, remint) == ()`
(`cernora_reference_workflow.candidate_development`), which permits only the
case-set-derived authority digests — and the `development_id` they feed — to differ
while pinning the hypothesis, the nine observations, the treatment axis, and
`treatment_sha256`.

## Independent review and remediation (2026-09-07)

An isolated read-only reviewer (not the Candidate's developer) examined this
worksheet, the record, the r9 custody chain, and the code paths. Verdict:
**admissible as the development basis for requesting a fresh held-out commitment**;
no P0 findings. Its findings and their dispositions:

- **P1 (repaired)** the treatment payload existed only as markdown here, so the
  re-mint had no byte-level input: committed `candidate-prompt.json`.
- **P2 (repaired)** no mechanical continuity gate existed between this freeze and
  the study re-mint: added `candidate_continuity_violations` plus
  `tests/unit/test_candidate_continuity.py`.
- **P2 (repaired)** the docs did not state that the re-minted record carries a
  different `development_id`: stated here and in the decision record.
- **P3 (repaired)** the failure enumeration omitted r8 (five rounds, not four).
- **P3 (recorded, wording)** the treatment rules cover the six behaviors the frozen
  verifier asserts plus two RFC-4180-public rules it does not (quotes honored only
  at field boundaries; embedded newlines). The mid-field-quote quadrant is probed
  by neither the frozen verifier nor a literal reading of the treatment sentence,
  while the frozen reference solution rejects it (`ValueError`). Both readings pass
  the visible verifier; a future held-out verifier could bind either. Known
  interpretation risk, accepted rather than re-minted.
- **P3 (recorded, wording)** the hypothesis mechanism's failure-family unification
  is inference beyond the value-free evidence, and names four of the six non-csv
  codes. Pre-registered hypothesis text; not operational in the falsifier.
- **P3 (recorded, reference)** `expected_observation`'s "declared threshold" refers
  to the standing design declaration (+10 pp paired RSR, `next-priority4-study-decision.md`),
  frozen at study materialization; the record is not self-contained on its success
  criterion.
- **P3 (deferred to ④)** the standing 9 × 2 × 3 / 54-Trial design geometry predates
  this record and must be re-derived at study materialization (a twelve-Case split
  implies 72 Trials at k = 3).

## Authorization record

The session mandate (2026-09-07) directed this work package under the
publish-then-act protocol. Two publication prompts (hypothesis and patch) timed out
unanswered (the session's sixth and seventh AskUserQuestion timeouts); work continued
under the session's standing mandate chain with the timeouts recorded, per the
campaign's established convention. All steps in this package are offline derivations;
no live authority, custody, or provider resource was consumed, and no held-out
material was requested or revealed.
