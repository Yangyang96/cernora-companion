# P4 Held-out Custodian Brief

Status: **operational; to be handed to an isolated custodian session; not a
StudyIntent, held-out reveal, smoke, or execution directive**

You are the independent custodian for the held-out commitment of the
Priority 4 controlled study. You have no knowledge of the study's Candidate,
its treatment, or its development evidence, and you must not acquire any.
Your sole deliverable is a sealed, content-hidden commitment of exactly
three fresh synthetic Python repair Cases, plus a value-free report. You
never print Case content.

This brief is self-contained. The repository is
`/Users/youda/workspace/cernora-reference-workflow/.agent/worktrees/p4-controlled-study`
(your working directory). The private custody root is `~/.cernora/p4-heldout/`.
Everything you do is offline: no network, no Docker, no provider
credentials, no live Runtime.

## Blindness contract (hard boundary)

You may read:

- this brief;
- `src/cernora_reference_workflow/heldout_seal.py` and
  `scripts/create_m4_heldout_seal.py`;
- `src/cernora_reference_workflow/controlled_task.py` — only the
  `_Heldout*` projection contracts, `load_visible_task`'s four-file shape,
  and `task_from_revealed_case`;
- `tests/unit/test_heldout_seal.py` and
  `tests/unit/test_controlled_task.py`;
- `pyproject.toml`, `uv.lock`, and this brief's directory.

You must not read, search, or otherwise inspect: `examples/` (in particular
`examples/priority4-development-pilot/` and `examples/m4-heldout-sealed/`),
`docs/`, `preparations/` outside this brief's directory, `.agent/`,
`ROADMAP*`, `README.md`, git history (`git log`, `git show`, `git grep`),
or any other session's memory files or transcripts; and you must not grep
the repository for campaign terms (csv, quoted, treatment, candidate, held,
r9, …). Those locations contain the treatment text, the development
observations, and their failure history. Authoring with that knowledge
would compromise the held-out boundary. If a step in this brief cannot be
completed inside these limits, stop and report the blocker instead of
widening your reading.

## Authoring scope (from the public study declaration)

Author exactly **three** held-out Cases (the seal machinery hard-limits the
archive to three) in the domain: **scanning delimited text with quoted
fields (CSV-like parsing) in Python**. Each Case is one small repair task:

- an allowed source module `src/<target>.py` containing a plausible but
  subtly wrong baseline implementation of a quoted-field scanner — the
  failure mode and the behavioral quadrant it emphasizes are your own
  design; choose variety across the three Cases and do not imitate any
  file you were forbidden to read;
- a protected frozen verifier `tests/verify.py` — deterministic, offline,
  and fast — which the reference implementation passes and the baseline
  fails;
- a reference solution (the correct implementation) that stays private and
  is never placed in the repository or in the archive;
- per-Case metadata: an opaque Case id `case-<32 lowercase hex>` (sorted and
  unique across the three), exactly one primary failure code in
  `<topic>_<mechanism>_v1` style, and a per-Case instruction mirroring the
  frozen task style: `Repair <target> so the frozen verifier passes.`

Calibration bar, verified offline per Case and reported only as booleans:
the reference solution passes the verifier with exit 0, and the baseline
fails it with a non-zero exit.

## Procedure

1. Author the three Cases under the private root, one directory per Case
   (e.g. `~/.cernora/p4-heldout/authoring/<case>/` containing
   `src/<target>.py`, `tests/verify.py`, and `solution.py`). Calibrate each
   Case there by running the verifier against the baseline and against the
   reference in a scratch copy. Never run Python against Case files inside
   the repository.
2. Assemble the plaintext archive as canonical JSON of `HeldoutArchive`
   (schema `cernora.reference.heldout-archive/v1`): a `cases` list of
   exactly the three `HeldoutArchiveCase` objects, each with `case_id`,
   `task`, `workspace`, `evaluation` mirroring the field shapes in
   `tests/unit/test_heldout_seal.py` (`cernora.reference.heldout-task/v1`,
   `cernora.reference.heldout-workspace/v1`,
   `cernora.reference.heldout-evaluation/v1`). The workspace `files` carry
   the baseline module and the verifier; the evaluation carries the command
   `["python", "tests/verify.py"]`, `working_directory` `"."`, a small
   `timeout_seconds`, `network` `"disabled"`, `expected_exit_code` `0`,
   `success_metric` `"verifier_exit_zero"`, and the Case's single failure
   code.
3. Validate offline before sealing, using the repository venv
   (`.venv/bin/python`): load the archive bytes through
   `HeldoutArchive.from_bytes` (it enforces canonical JSON), and project
   every Case through
   `cernora_reference_workflow.controlled_task.task_from_revealed_case`
   (it enforces the projection contracts and path classification). Do not
   modify contract code; fix your archive until validation passes.
4. Write the canonical plaintext to `~/.cernora/p4-heldout/archive.json`
   with permissions `0600`. The plaintext, the reference solutions, and
   the authoring tree stay outside the repository permanently.
5. Seal from the repository root:

   ```sh
   .venv/bin/python scripts/create_m4_heldout_seal.py \
     --archive ~/.cernora/p4-heldout/archive.json \
     --output-dir preparations/p4-heldout-commitment \
     --key-output ~/.cernora/p4-heldout/reveal.key
   ```

   The script refuses to place the archive or the key inside any Git
   worktree; it writes exactly `ciphertext.bin` and `manifest.json` into
   the output directory plus a `0600` reveal key. Never print the key,
   the plaintext, or any Case content.
6. Optionally sanity-run the two permitted unit-test files
   (`pytest tests/unit/test_heldout_seal.py
   tests/unit/test_controlled_task.py`). Do not run the whole suite and do
   not touch `examples/`.
7. Commit the public output directory only, with the message
   `feat(heldout): seal three fresh held-out cases`. If unsure about
   committing, leave the directory for the user and say so in the report.

## Report (the only content that crosses to the development session)

- the `manifest_id` and the SHA-256 of `manifest.json`;
- the case count (3);
- per-Case calibration booleans (reference passed; baseline failed);
- the reveal-key custody path (the path only, never the bytes);
- any blocker, verbatim and value-free.

Case ids and the manifest are public after the commit; the manifest never
carries Case content. Nothing else crosses: no Case text, no repository
locations of private material beyond the paths above.

## Prohibitions

- No Docker, no network, no provider credential use, no live Runtime, and
  no writes outside the repository and the private custody root.
- No changes to contract or source code. If validation fails, fix the
  artifact, never the machinery.
- No printing of plaintext, key material, or Case content.
- The commitment is one-shot: once the seal is published, it is never
  re-sealed or revised. If it is later found unsatisfactory it is retired
  whole and a fresh custodian round starts.
