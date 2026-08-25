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

## Commands

Run from a clean independent checkout:

```sh
uv sync --frozen --all-groups
uv run python scripts/verify_public_wheel.py
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/verify_release.py
git diff --check
```

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
