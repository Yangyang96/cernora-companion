# P4 second cleanup

This local cleanup removes the remaining retired research producers after preserving the
first-round working tree as `before-round2.tar.gz` in a separate private archive named
`p4-cleanup-round2-2026-09-09`. The original 72-Trial evidence and exact historical wheels
remain in `p4-72-trials-2026-09-09`; they are not replaced by newly built wheels.

## Removed and retained responsibilities

- Removed the unused parallel `ControlledExecutionResult`, `ControlledTrialExecution` and
  `ControlledAttemptExecutor` types. Current immutable Attempt records, integrity checks,
  retry handling, live executor safe stops and execution recovery remain.
- Retired `study_preparation.py` and its worksheet command. Historical preparation JSON
  remains reference material; current callers provide the existing `StudyIntent` contract.
- Retired `improvement_loop.py` and the old M4 final-plan producer. The still-used specification
  builder and image authority types moved to `controlled_spec_builder.py`; their behavior was
  preserved rather than generalized. `create_m4_task_images.py` remains because P4 image
  construction still calls its image-building helpers.
- Moved only the case/plan fixtures used by current Study tests to `tests/support/study_cases.py`.
  Tests dedicated to the retired producers were removed; current matrix, held-out and recovery
  tests remain.
- Moved adversarial fixture generators to `tests/support`. Their tests remain. These are now
  source-checkout maintenance tools, outside the installed production package. Invoke the
  retained generator with `uv run python -m scripts.generate_derived_matrix`.
- Marked research dossiers as historical and removed the retired worksheet from active setup
  instructions. Historical material is available without becoming a new consumer's checklist.

Core and its public contracts are unchanged. The large Study, execution and live-adapter
implementations have not been rewritten. A fresh live experiment, observ-cli integration,
release and Git publication are outside this cleanup.

## Size accounting

| Python lines | After first cleanup | After second cleanup | Net decrease |
| --- | ---: | ---: | ---: |
| Package source | 19,191 | 17,452 | 1,739 |
| Scripts | 4,778 | 4,493 | 285 |
| Tests and test support | 15,105 | 14,565 | 540 |
| Total | 39,074 | 36,510 | 2,564 |

Of the package decrease, 410 lines were relocated into test support rather than deleted.
The total above includes the relocated code. Across both rounds, package source decreased from
24,437 to 17,452 lines, approximately 29%.

## Compatibility and evidence

These are unpublished Companion research interfaces. Historical tool consumers must use the
archived revision; no compatibility shims keep the retired producers in the current package.
The original final evidence remains `regressed`, not proof of positive Agent improvement.

Before and after migration, the same nine-case/two-configuration inputs produced 18 identical
Experiment IDs and specification byte hashes. The first-round snapshot and per-file hashes
allow this cleanup to be reviewed or reversed independently of the original result archive.

## Validation

- Complete offline suite: **547 passed** in 913.88 seconds, including retained adversarial,
  Study custody/recovery, full-matrix execution and byte-stable evidence-pack tests.
- Ruff check, formatting and Git whitespace checks passed.
- Strict mypy passed for the 60 source/script files. Full-tree checking still reports the same
  23 pre-existing test typing errors in three script-test modules; no new errors were introduced.
- Final wheel and source distribution built successfully. In an isolated wheel environment,
  all 42 package modules imported, all 13 retired module names across both rounds were absent,
  and the active safe-stop behavior remained unchanged.
- The migrated builder produced 18 identical specification byte hashes and Experiment IDs.
- The newly built Companion wheel, with the archived Core wheel and dependencies, rebuilt the
  original 72-Trial evidence with socket creation disabled: all 702 files matched byte-for-byte.

At cleanup verification time, no live Agent run, commit, push or release was performed.
The exact historical toolchain remains
separately archived; current callers do not need to restore the retired research producers.
