# pi runtime base image

This image replaces `../codex-runtime` as the pinned live Runtime base for the
companion Repeat Runner. It pins:

- Node.js `22.23.2` (official Linux arm64 glibc tarball, SHA-256 verified);
- `@earendil-works/pi-coding-agent` `0.84.4` plus its full dependency tree,
  installed only from the committed `package-lock.json`.

The committed `Dockerfile` performs no network access of its own — every layer is
a `COPY` plus digest-verified unpacking, so `docker build` is immune to registry
or daemon proxy outages. Context preparation below does fetch the pinned
artifacts (Node.js tarball and the `npm ci` dependency tree); that is the only
step that ever needs the network, and both artifacts are verified by digest or
by the lockfile's integrity records. Two large context inputs are not committed
to Git; prepare them from this directory first:

```sh
cd images/pi-runtime

# 1. Vendor the exact Node.js distribution (digest is verified by the build).
#    nodejs.org may be slow on direct links; the npmmirror CDN serves the
#    identical, officially-checksummed artifact.
curl -L -o node-v22.23.2-linux-arm64.tar.gz \
  https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-arm64.tar.gz
shasum -a 256 node-v22.23.2-linux-arm64.tar.gz
# expected: 013b59cfd2819703a6f4a14ab891fc46fc2a4e3f5bcd92de3fb4929b43e35b30

# 2. Vendor the dependency tree from the committed lockfile.
#    Use the registry your environment can reach (the lockfile pins exact
#    tarball integrity, so mirrors are safe).
npm ci --omit=dev --ignore-scripts --no-audit --no-fund

# 3. Build and record the resulting image content digest.
docker build -t cernora-reference/pi-runtime:0.84.4 .
docker image inspect --format '{{.Id}}' cernora-reference/pi-runtime:0.84.4
```

Pin the printed image ID (without the `sha256:` prefix) as `BASE_IMAGE` in
`src/cernora_reference_workflow/spec_builder.py`, then rebuild the task images
and pin their new digests in `TASK_IMAGE_SHA256`.

Notes:

- The six `@earendil-works` workspace packages nested under `pi-coding-agent`
  are published without lockfile integrity records. Their install URL is
  pinned by the lockfile, and the whole tree is byte-frozen afterwards by the
  image content digest and the runtime check command.
- The runtime check (`PREINSTALLED_PI_CHECK_COMMAND` in
  `src/cernora_reference_workflow/runtime_policy.py`) verifies `pi --version`,
  the lockfile digest, and the installed entrypoint without any network
  access, mirroring the historical Codex fail-closed contract.
- `node_modules/` and the tarball are intentionally untracked (see the
  repository `.gitignore`).
