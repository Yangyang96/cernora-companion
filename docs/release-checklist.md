# Private publication checklist

Keep this repository and all generated artifacts private until every item below has observed,
reviewable evidence. Running a subset is not publication approval.

## Source and dependency boundary

- [x] The exact `cernora==0.1.2` public wheel installs in a clean CPython 3.12 and 3.13 project
      with the Cernora source checkout unavailable.
- [x] The recorded wheel digest matches the downloaded artifact used by import, evaluation, and
      strict reload.
- [x] Harbor, pi, Node.js, Python packages, and the task image are exactly pinned.
- [ ] The shipped license inventory includes Harbor's Apache-2.0 license, the pi runtime's MIT
      license, Node.js, and the full npm dependency tree. The inventory generator still covers
      only the Python lockfile; extend it to the `images/pi-runtime` package-lock tree before
      this item can be checked.
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

- [x] A real pi attempt supplies the successful repair export: the pi-era native acceptance
      execution (2026-09-03, `reports/private/m1-native-acceptance-pi-20260903-r2`, DeepSeek
      `deepseek-v4-flash`) closed 3/3 v1 repair Trials as `pass` with strict-reload evaluations,
      and the standalone v1 and v2 tracer runs both passed end-to-end.
- [ ] A different real completed pi attempt supplies the behavioral failure export. In the
      pi-era runs so far the v2 task was solved or timed out instead of failing behaviorally;
      the behavioral-failure class is currently covered only by deterministic derived
      mutations from the successful export.
- [x] Real timeout and interruption attempts are frozen without automatic retry.
- [x] Missing-artifact, digest-mismatch, authority-mismatch, and planted-secret cases are labeled as
      deterministic derived mutations with source digest and recipe identity.
- [x] Valid failing evidence fails; corrupt, missing, mismatched, or unverifiable evidence is
      inconclusive; secret detection rejects publication.

## Native acceptance

- [x] The complete tracer passes on macOS Apple Silicon with the pinned image.
- [x] External provider auth is injected from the explicit `PI_AUTH_JSON_PATH` file into only
      the ephemeral Runtime home and is removed during cleanup; the cleaned receipt is
      verified under agent timeouts (shielded cleanup) and redaction scans stay clean.
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
- [x] A native 12-Trial live run has been observed with the one qualified source-tree Harbor/pi
      connector on macOS Apple Silicon (`reports/private/m1-native-acceptance-pi-20260903-r2`:
      2026-09-03, 12/12 Trials, 12 Attempts, zero retries; 3 `pass` and 9 timeout-derived
      evaluation-invalid Trials, `M1-native-acceptance` identity accepted before execution).
      This is a manual acceptance item, not a CI claim; the historical Codex run remains
      recorded but is no longer connector evidence.
- [ ] A pi-era offline fixture trio replaces `examples/m3-offline` for the current release
      verifiers. The committed Codex-era trio is frozen in place as historical evidence
      (pinned by `tests/unit/test_runtime_era_boundary.py`) and is verified with the
      Codex-era revision of this repository, not by the current contracts.
- [x] The accepted Execution retained a graceful `stopped` checkpoint (after Trial 7, at the
      operator SIGINT boundary request) and resumed the same identity to `completed`
      (2026-09-03, pi/DeepSeek run): strictly rebuildable pass Evaluations for the v1 repair
      cell and genuine unavailable lifecycle evidence from the timeout cells.
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

## Priority 4 Milestone 3 Controlled Comparison local candidate

- [x] Companion `0.3.0` installs with the exact accepted Cernora Core `0.1.4` wheel in isolated
      CPython 3.12 and 3.13 environments; neither artifact is described as publicly released.
- [x] Controlled ExperimentSpec/RunPlan V2 derives each Experiment ID from canonical Core
      authority before execution and rejects projection, identity, matrix, or Treatment mismatch.
- [x] Legacy M1/M2 identities remain byte-semantics compatible for their historical readers and
      are rejected, not rewritten, at the M3 controlled-comparison boundary.
- [x] `experiment compare` accepts only a strict Core Batch Summary package plus an exact V2
      RunPlan and ComparisonPlan, checks every Trial and Evaluation receipt binding, and publishes
      through Core's atomic Comparison API into a new directory.
- [x] Adversarial coverage proves that timeout, resources, retry, dataset, Profile, report, or
      statistical differences cannot be hidden by caller-supplied equal arm declarations or a
      forged Treatment endpoint.
- [x] Three publications from the same frozen inputs are byte-identical and strictly reload to
      the same Comparison and Summary identities with sockets denied.
- [x] Companion reports valid Core conclusions honestly, including `not_comparable`, `uncertain`,
      `no_change`, `mixed`, and `regressed`, without winner, ranking, or promotion semantics.
- [x] Full tests, Ruff, format, strict mypy, build, release preflight, wheel-only acceptance,
      secret scan, personal-path scan, and independent review pass without push, tag, upload, or
      public Evidence publication.

## Commands

Run from a clean independent checkout:

```sh
# First place the accepted Core 0.1.4 wheel in ../cernora/dist; its SHA-256 must be
# 5b847837b7182b3ece8054eb5187fde4f835582787b406ea4a7f2f8bd2987a4c.
uv sync --frozen --all-groups --offline
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/verify_release.py
git diff --check
```

After building the Companion candidate wheel, run `scripts/verify_comparison_wheels.py` with the
exact Core and Companion wheels. The verifier installs only those project wheels, tests both
supported Python minors, denies Python network sockets, publishes three comparisons per minor,
strictly reloads each result, and requires byte-identical authoritative trees. Release preflight
reports this wheel-only gate as a separate requirement; its source-level result does not claim
wheel-only acceptance.

The accepted M2 `scripts/verify_batch_wheels.py` gate and retained Pack remain historical evidence
for the 0.1.3/0.2.1 pair. M3 does not reinterpret or rerun that acceptance as a 0.1.4/0.3.0 claim.

The historical `scripts/verify_public_wheel.py` and checked-in remote workflow remain the accepted
`v0.2.0` checks for the public Core `0.1.2` boundary; they are not M3 candidate gates. Remote M3 CI
activation is deferred until a separately authorized publication step makes Core `0.1.4`
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
