# P4 Study Materialization Mechanics (⑤ worksheet)

Status: **engineering scoping frozen by in-session survey; implementation
pending; no held-out reveal, no live authority, no provider resource consumed**

This worksheet freezes the mechanical path from the two terminal artifacts —
the frozen development Candidate
(`preparations/p4-candidate-development-csv-quoted`, record `1b0a6249…`) and
the sealed held-out commitment
(`preparations/p4-heldout-commitment`, manifest `9b23e459…`, commit
`1a4259f`) — to a prepared, reveal-bound study that stops immediately before
the live 72-Trial execution. It cites the enforcing code for every
constraint so the next implementation pass cannot drift.

## Operating sequence

1. **Out-of-band reveal — user authorization boundary R** (the first
   held-out content access by the development side). Decrypt the seal with
   the custodian's key (`~/.cernora/p4-heldout/reveal.key`) via
   `reveal_heldout_archive` (`heldout_seal.py:336`), binding the receipt to
   the frozen Candidate content (`candidate_freeze_id` = `1b0a6249…`,
   `candidate_freeze_sha256` = sha256 of the frozen record's canonical
   bytes). Project each decrypted case through
   `task_from_revealed_case` (`controlled_task.py:447`), which re-validates
   the byte-exact round-trip and path classification, and re-derive each
   case's digest (`sha256(canonical_json(reconstructed_revealed_case(task)))`)
   to confirm it equals the public manifest commitment
   (the `improvement_loop.py:428-446` check). Emit exactly three
   `ControlledTaskAuthority` canonical JSONs into a private task root and the
   reveal receipt into the study custody. The sealed case content is thereby
   bound to the public commitment before any use.
2. **Held-out task images — offline.** Build the three case images from the
   revealed workspaces, twice each with no cache (the existing dual-build
   procedure; offline COPY context is the proven network-free mode), and
   record the twelve-Case image authority set: the nine visible Cases keep
   their frozen r9 image digests; the three held-out Cases get the new
   digests; `build_base_image` stays the pinned pi base image.
3. **Materialize the twelve-Case plan, comparison, and Intent — offline.**
   - nine visible task authorities via `load_visible_task`
     (`controlled_task.py:387`) over `examples/priority4-development-pilot`;
   - configurations from byte-frozen prompt authority sources: baseline
     `p4-confirmatory-baseline-prompt-v1` (payload
     `{"text": "Repair the task from its declared behavior and the available
     workspace evidence."}`, source SHA-256 `acf0b631…`) and candidate
     `p4-confirmatory-candidate-prompt-v1` (the committed
     `candidate-prompt.json`, source SHA-256 `118ae581…`);
   - `build_controlled_specifications` (`m4_final_plan.py:396`) with
     `timeout_seconds=1800`,
     `bootstrap=case-clustered-paired-bootstrap/v1` (9,500 bp, 10,000
     resamples, `nearest_rank_closed`, `comparison_input_sha256`), and
     `pass_k` k=3;
   - the `ControlledRunPlanV2` over all twelve tasks × 2 configurations × 3
     repetitions: 72 planned Trials, `max_attempt_count` 144,
     `max_total_wall_time_seconds` 160,000, repetition-major adjacent
     AB/BA blocks (the `compile_study_protocol` schedule:
     `controlled_study.py:413`), and the frozen plan-boundary vocabulary —
     `companion_version="0.4.0"` and `analysis.method_version="m4"` —
     enforced by `bind_study_run_plan` (`study_projection.py:94`);
   - the `ComparisonPlanV1` with split-scoped primary outcome
     (`paired-reliable-success-rate-delta` on `held-out`, +10 pp practical
     threshold at 1,000 basis points) and the three frozen guardrails
     (evaluation-validity hard at 0 adverse, protected-path hard at 0
     adverse, regression-RSR no worse than −1,000 basis points);
   - the `StudyIntent` whose `cases` authorities are `case_authority_sha256`
     projections of the plan (including the three revealed Cases), whose
     Candidate record is **re-minted** by `freeze_candidate_development`
     with the plan-derived `configuration_authority_sha256` values and the
     byte-copied hypothesis, treatment, and nine observations, asserting
     `candidate_continuity_violations(prior, remint) == ()`
     (`candidate_development.py:130`);
   - `heldout_commitment`: `manifest_sha256` = SHA-256 of the sealed
     manifest's canonical bytes (`9a99c792…`), `case_count` 3,
     `case_commitment_root_sha256` = SHA-256 over the intent's own three
     held-out Case entries, and `reveal_policy_sha256` = digest of the
     frozen reveal policy (this dossier's boundary R conditions);
   - the frozen analysis policy and the `ImplementationLock` binding the new
     dual-built Companion wheel bytes (0.4.1 runtime lineage), the pinned
     Cernora wheel, the pi adapter/harness identity, and the analysis
     policy digest.
4. **Custody ceremony — offline.** `experiment study prepare <intent>` then
   `advance` `request-reveal` then `bind-reveal` (the reveal payload repeats
   the intent's held-out entries; `controlled_study.py:1255-1279` enforces
   equality with the commitment root), stopping at `AwaitingAcceptance`.
5. **Stop.** `start-execution` — the live 72-Trial matrix — is user
   authorization boundary L, outside this package.

## Hard constraints (with enforcing sites)

- The intent cannot be frozen before the reveal: its Case authorities
  (including held-out) are `canonical_content_id(spec.core_authority().case)`
  (`study_projection.py:17`) derived from the post-reveal plan. The m4-era
  flow has the same shape: `create_m4_final_plans` consumed revealed task
  JSONs and the reveal receipt. The order is therefore reveal → images →
  plan → intent → prepare → ceremony, with the state machine recording the
  reveal that the materialization already consumed.
- `bind_study_run_plan` (`study_projection.py:85`) fails closed on every
  mismatch: plan boundary Literal, configuration order, budgets
  (`repetitions`, planned Trials, attempts, wall seconds), Case-set equality,
  per-Case authority digests, record configuration digests, and the exact
  AB/BA trial-slot coordinate sequence.
- `HeldoutReveal.cases` must byte-equal the intent's held-out entries and its
  root the commitment root (`controlled_study.py:1272-1279`).
- `StudyIntent` requires all three splits present, the held-out count equal
  to the commitment's, and every record observation's case/split present
  with a matching split (`controlled_study.py:284-299`); the nine
  observations are six development plus three regression cases.
- Observations that are passes (`slug-collapse`, `filename-sort`) carry
  `agent_outcome: "pass"` and `failure_code: null` — keep byte-copied.
- Test-suite discipline: `test_study_control_plane` writes custody under
  `.agent/test-controlled-study` with flock serialization; run the full
  suite exclusively and clear that directory first.

## New tooling (implementation pass)

- `scripts/create_p4_heldout_reveal.py` — boundary R: decrypt → verify →
  emit three private task-authority JSONs + the candidate-bound reveal
  receipt. Refuses to run without an explicit authorization flag; prints
  only value-free identities.
- `scripts/create_p4_study_images.py` — build the three revealed task images
  twice (no cache) and publish the twelve-Case image authority set.
- `scripts/create_p4_study_materialization.py` — assemble specs, plan,
  comparison plan, re-minted record, intent, and lock; run the prepare and
  reveal ceremony up to `bind-reveal`; publish the identities.
- Unit tests throughout with fake held-out tasks (mirroring
  `test_study_control_plane.py` fixtures); no test touches the real seal.

## Authorization record

The ④ adjudication (2026-09-08) and the custodian delivery set this package
in motion. Boundary R (reveal) and boundary L (live execution) each remain
separate user decisions; nothing in the implementation pass requires either.