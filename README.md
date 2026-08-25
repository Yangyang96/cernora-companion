# Cernora Reference Workflow

This private-by-default companion repository implements the Cernora Priority 3 reference
coding-Agent workflow. It runs the exact approved Harbor and Codex versions locally, freezes
a closed completed export, and performs all adaptation and evaluation offline through the
public `cernora==0.1.2` wheel.

The project is not part of Cernora Core. It owns orchestration, export validation, the
`cernora-reference-coding-v1` Profile, and portable reports. It does not provide a generic
Runtime connector or claim network isolation while Codex provider egress is enabled.

## Offline quality gate

```sh
uv sync --frozen --all-groups
uv run python scripts/verify_public_wheel.py
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/verify_release.py
```

The public-wheel verifier requires the public Package Index. Frozen export evaluation is
offline. `.github/workflows/offline.yml` runs the same frozen-evidence gate on Linux with
CPython 3.12 and 3.13; it contains no credentials and never invokes the live tracer. Live
authenticated execution is a separate manual command:

```sh
CODEX_AUTH_JSON_PATH=/absolute/path/to/auth.json \
  uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1.json
```

The real lifecycle matrix uses separately identity-bound inputs:

```sh
# Genuine completed behavioral-failure candidate; the task remains authoritative.
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v2.json

# Three-second effective Agent timeout bound into its own ExperimentSpec.
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1-timeout.json

# Real SIGINT sent to the active in-container Codex process.
uv run python scripts/run_tracer.py --spec examples/tiny-calculator-v1-interruption.json \
  --operator-interrupt
```

Every live command requires the external `CODEX_AUTH_JSON_PATH` environment variable. The v2
task is the separately versioned harder task permitted by the Priority 3 baseline; it never
mutates or relabels the successful v1 export.

The pinned Runtime binds `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` to
`http://host.docker.internal:9981`. That endpoint is part of the Runtime and Experiment identity,
contains no credentials, and must resolve to the operator-authorized local provider proxy. Changing
or removing it requires a new Runtime image and new ExperimentSpecs.

## Pinned image acquisition and offline task rebuild

Acquire the exact Codex Runtime archive and build its verified Runtime image from the repository
root. The Dockerfile checks the archive and native binary hashes before creating the tag:

```sh
docker build --platform linux/arm64 \
  --tag cernora-reference/codex-runtime:0.148.0 images/codex-runtime
```

Once that exact Runtime image is local, the two task layers rebuild without network or image pulls:

```sh
docker build --network=none --pull=false --platform linux/arm64 \
  --tag cernora-tiny-calculator:priority3 tasks/tiny-calculator-v1/environment
docker build --network=none --pull=false --platform linux/arm64 \
  --tag cernora-tiny-calculator:priority3-v2 tasks/tiny-calculator-v2/environment
```

Compare the resulting local content IDs with `task.toml` and the checked-in ExperimentSpecs before
using the live tracer. The tracer repeats this identity and platform check before reading auth.

After the successful v1 export exists, derive the labeled fail-closed matrix from that exact
frozen source. The output directory must not already exist and remains private under `exports/`:

```sh
uv run python scripts/generate_derived_matrix.py \
  --spec examples/tiny-calculator-v1.json \
  --export exports/<successful-export> \
  --output exports/<successful-export>-derived-matrix
```

The matrix atomically publishes four identity-bound mutations and records that a provably fake
credential was rejected before publication. It never describes those fixtures as live Runtime
failures.

Never place authentication files, attempts, raw Runtime homes, or unpublished exports in the
repository. See `docs/architecture.md`, `docs/compatibility.md`, and
`docs/release-checklist.md` before operating or reviewing the workflow.
