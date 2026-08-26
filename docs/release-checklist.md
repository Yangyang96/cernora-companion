# Private publication checklist

Keep this repository and all generated artifacts private until every item below has observed,
reviewable evidence. Running a subset is not publication approval.

## Source and dependency boundary

- [x] The exact `cernora==0.1.2` public wheel installs in a clean CPython 3.12 and 3.13 project
      with the Cernora source checkout unavailable.
- [x] The recorded wheel digest matches the downloaded artifact used by import, evaluation, and
      strict reload.
- [x] Harbor, Codex, Python packages, and the task image are exactly pinned.
- [x] The shipped license inventory includes Harbor's Apache-2.0 license and all dependencies.
- [x] `git status` contains no auth material, attempt state, local Agent state, private export, or
      host-specific path.

## Contract and adversarial boundary

- [x] ExperimentSpec, completed export, and run report reject unknown members, bad identities,
      unsafe paths, missing files, unexpected files, invalid lifecycle states, and digest mismatch.
- [x] The planted fake credential is rejected before export or report publication.
- [x] Repository content, staged exports, adapted bundles, Cernora outputs, and reports pass the
      deterministic secret scan.
- [x] Missing diagnostic data is represented by an explicit missing marker, never an invented
      zero or empty artifact.
- [x] JSON Schema validation and semantic model validation both pass for accepted artifacts.

## Cernora boundary

- [x] Adapter and Profile conformance use only package-root APIs from the installed public wheel.
- [x] Frozen adaptation and evaluation perform no network, Runtime, Docker, Git, shell, or test
      execution.
- [x] Strict reload verifies the referenced EvidenceBundle and portable result identities.
- [x] Three evaluations of the same frozen export produce byte-identical Cernora output and
      byte-identical JSON and Markdown reports.
- [x] Offline rebuild commands use only relative paths and reproduce the recorded identities.

## Real and derived cases

- [x] A real Codex attempt supplies the successful repair export.
- [x] A different real completed Codex attempt supplies the behavioral failure export.
- [x] Real timeout and interruption attempts are frozen without automatic retry.
- [x] Missing-artifact, digest-mismatch, authority-mismatch, and planted-secret cases are labeled as
      deterministic derived mutations with source digest and recipe identity.
- [x] Valid failing evidence fails; corrupt, missing, mismatched, or unverifiable evidence is
      inconclusive; secret detection rejects publication.

## Native acceptance

- [x] The complete tracer passes on macOS Apple Silicon with the pinned image.
- [x] Subscription auth is injected from the explicit external auth-file path into only the
      ephemeral Runtime home and is removed during cleanup.
- [x] Effective telemetry settings and exported artifacts confirm telemetry is disabled.
- [x] Observed provider egress and disabled web search are documented without claiming network
      isolation.

## Priority 4 Milestone 1 Companion Repeat Runner

- [x] A deterministic offline conformance run closes the ordered 2 Cases × 2 Configurations × 3
      repetitions matrix as exactly 12 Trials.
- [x] Public conformance covers evaluated success, behavioral failure, timeout, retry-eligible then
      terminal non-retryable/unavailable, missing or duplicate matrix rejection, digest tampering,
      crash adoption, and hard Attempt-budget exhaustion.
- [x] RunPlan identity binds the embedded ExperimentSpecs, ordered matrix, repetitions, connector,
      retry scope, fixed concurrency, and Attempt/wall budgets.
- [x] Active records, Attempt artifacts, Trial results/manifests, and hash-chained checkpoints are
      append-only; ambiguous active Attempts fail closed on resume.
- [x] A completed execution produces a closed sidecar Execution Pack, and offline-only rebuild
      reproduces verified bytes without credentials, network, Runtime, Docker, Git, or shell.
- [x] Diagnostics state lifecycle and completeness only; M1 publishes no aggregate quality rate,
      ranking, winner, or comparative conclusion.
- [x] A native 12-Trial live run has been observed with the one qualified source-tree Harbor/Codex
      connector on macOS Apple Silicon. This is a manual acceptance item, not a CI claim.
- [x] The accepted Execution retained a graceful `stopped` checkpoint and resumed the same identity
      to `completed`; it contains 11 strictly rebuildable Evaluations and one naturally occurring
      non-retryable `runtime-pre-terminal-failure` with unavailable evaluation status.
- [x] The completed Pack strictly reloaded, rebuilt the Execution byte-for-byte, and all three
      portable trees passed credential, personal-path, Runtime-home, proxy-endpoint and undeclared
      file checks.

## Priority 4 Milestone 2 Batch Summary local candidate

- [x] Companion `0.2.1` installs with the exact local Cernora Core `0.1.3` wheel in an isolated,
      wheel-only environment; neither artifact is described as publicly released.
- [x] `experiment summarize PACK --output DIR` accepts only a strictly verified completed M1 Pack
      and a new output directory, then strictly reloads the published Core Batch Summary.
- [x] Normalization preserves the frozen RunPlan, ordered Trials, Attempt lineage, selected
      Evaluation Packages, lifecycle receipts, and authoritative available resource receipts.
- [x] Missing, conflicting, malformed, identity-mismatched, tampered, or concurrently changed Pack
      evidence fails closed without publishing a partial summary.
- [x] Conformance covers all four exhaustive outcomes: `pass`, `behavioral_fail`,
      `evaluation_invalid`, and `infrastructure_unavailable`; invalid Evaluation evidence is not
      relabeled as infrastructure failure.
- [x] Retry Attempts remain diagnostics within one Trial and never become independent Trials.
- [x] Three summaries from the same Pack in distinct new directories have byte-identical
      authoritative bytes and strictly reload to the same identities.
- [x] The retained native M1 Pack normalizes to exactly 5 `pass`, 0 `behavioral_fail`,
      6 `evaluation_invalid`, and 1 `infrastructure_unavailable` Trial.
- [x] M2 publishes no comparison, delta, interval, pass-at-k, `pass^k`, ranking, promotion,
      improvement decision, or winner.
- [x] The final local candidate passes full tests, Ruff, format, strict mypy, build, release
      preflight, wheel-only acceptance, secret scan, and personal-path scan without a push, tag,
      upload, or public Evidence publication.

## Commands

Run from a clean independent checkout:

```sh
# First place the accepted Core 0.1.3 wheel in ../cernora/dist; its SHA-256 must be
# 53276a35b137e4997ea5cdf843e2d23323583c4b34ac87a9cd997a08d44e6704.
uv sync --frozen --all-groups --offline
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/verify_release.py
git diff --check
```

After building the Companion candidate wheel, run `scripts/verify_batch_wheels.py` with the exact
Core wheel, Companion wheel, and retained accepted Pack. The verifier installs only those project
wheels, tests both supported Python minors, strictly reloads three summaries per minor, checks the
5/0/6/1 outcomes, denies Python network sockets while summarizing, and requires byte-identical
authoritative trees. Release preflight reports this wheel-only gate as a separate requirement; its
source-level result does not claim wheel-only acceptance.

The historical `scripts/verify_public_wheel.py` and checked-in remote workflow remain the accepted
`v0.2.0` checks for the public Core `0.1.2` boundary; they are not M2 candidate gates. Remote M2 CI
activation is deferred until a separately authorized publication step makes Core `0.1.3`
available without weakening the wheel-only boundary.

The authenticated tracer remains a separate manual command and is not part of CI:

```sh
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1.json
```

Run the remaining real cases with `examples/tiny-calculator-v2.json`,
`examples/tiny-calculator-v1-timeout.json`, and
`examples/tiny-calculator-v1-interruption.json --operator-interrupt` as documented in the README.
Each command uses a unique job name and may not replace an existing attempt or export.

After the successful export is frozen, generate its private deterministic failure matrix:

```sh
uv run python scripts/generate_derived_matrix.py \
  --spec examples/tiny-calculator-v1.json \
  --export exports/<successful-export> \
  --output exports/<successful-export>-derived-matrix
```

## Authorization

- [ ] An explicit human review confirms all gates above from the final bytes.
- [ ] The repository owner explicitly authorizes commit, push, pull request creation, and public
      publication as separate actions.
