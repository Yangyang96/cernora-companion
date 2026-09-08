# P4 result preservation and first cleanup

The 72-Trial study completed with 72 Attempts and no retries. It produced 18 passes,
33 behavioral failures, and 21 infrastructure-unavailable outcomes. Held-out RSR was
8/9 for baseline and 1/9 for candidate; the comparison concluded `regressed`.
This is evidence of a completed evaluation workflow, not positive Agent improvement.

## Historical reproduction

The pre-cleanup Companion revision is `5790ff6379bae80818602b2e1ca0dea29c2eaf72`.
The historical Core revision is `7b46258457142cfd32bc8c7ea46bc21740b475de`.
A private local archive named `p4-72-trials-2026-09-09` retains tracked source snapshots,
Git history bundles, exact project wheels, final custody, materialization and image authority,
and a source-isolated offline reconstruction script. Its inventory is SHA-256 indexed.
The archive is local preservation, not a remote backup or a published release.

Exact historical wheels:

- Core 0.1.4: `4ef10a5eb2f9961943883576ab81bc97ce32d2f3f8a88cb9679d5c51c81e368d`
- Companion 0.4.2: `6b8acee8b58017597d13931b5427f795e6981287ea4da877ca81ae7c79f3316b`

Use those bytes to reconstruct the historical study. A new wheel built from this working
copy has different bytes even while its unreleased display version remains 0.4.2; it must
never replace the wheel in the historical ImplementationLock. Versioning a new release is
outside this cleanup.

## Retired local research tooling

Removed from the current package and scripts:

- Development Pilot corpus execution, versioned Pilot bundles, Pilot custody and step loops,
  and one-off candidate-freeze/image/bundle producers.
- Runtime diagnostic Pilot orchestration and out-of-band probes.
- The parallel M4 controlled runner, execution store, batch wrapper, and `run_m4_controlled.py`.

Their dedicated tests are retained with the same source snapshot, rather than continuing to
test features removed from the current package. Historical preparation JSON, examples and
research dossiers remain as reference material, including fixtures still consumed by Study
tests. Commands in those historical dossiers require the pre-cleanup revision.

The ordinary Runner, resume/recovery, immutable Attempt contracts, evidence checks,
Batch/Comparison semantics, Controlled Study and current live adapter remain. Active safe-stop
semantics moved unchanged into the existing shared execution module. Core contracts were not
changed. No new Study abstraction or runtime lifecycle was introduced.

## Scope and next consumer

This round removes independently retired implementations; it does not establish that the
ordinary CLI is a complete live controlled-study driver. The 72 Trials used a temporary live
loop; that temporary file was no longer present when preservation began. Closed evidence can
be reconstructed offline, but this archive does not promise a fresh live rerun.

Next, validate an observ-cli change-context improvement against a fixed downstream Agent,
then add a thin Chora task-evaluation integration. These consumers should determine the next
cleanup and the minimum reusable interface. Positive improvement remains a separate milestone.

## Validation of this cleanup

- The complete remaining suite ran: 565 passed; 14 failed solely because the sandbox
  denied writes to the repository-local test custody directory. Rerunning all three affected
  test modules with that directory writable passed all 24 tests, including all 14 failures.
  Across these runs, all 579 remaining tests passed.
- Ruff check, formatting check and Git whitespace checks passed.
- Strict mypy passed for all 66 source/script files. Full-tree mypy reports 23 errors in
  three unchanged script-test modules; the archived pre-cleanup source reproduces the exact
  same errors. Those existing test typing issues remain outside this removal.
- Wheel and source distribution builds passed. A source-isolated wheel smoke check imported
  all 46 remaining modules and confirmed the eight retired modules were absent.
- Two independent historical-wheel offline reconstructions each reproduced all 702 evidence
  files byte-for-byte, with socket creation disabled.

Package Python source decreased from 24,437 to 19,191 lines (5,246 fewer, approximately 21%).
This record captures the local cleanup verification before Git commits were created.
No release or publication was performed.
