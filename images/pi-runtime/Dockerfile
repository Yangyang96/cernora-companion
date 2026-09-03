# Codex runtime base replacement: pinned Harbor pi runtime base.
#
# The image is built from a prepared, verifiable local context:
# - the Python base is the digest-pinned image that also backed the historical
#   Codex runtime base and all task images;
# - the Node.js runtime is the official Linux arm64 glibc distribution
#   tarball, verified by SHA-256 before extraction (libstdc++ and libgcc are
#   already present in the Python base, so no package install is needed);
# - the pi coding agent and its dependency tree are vendored locally by
#   `npm ci` from the committed package-lock.json and copied in verbatim.
#
# Nothing in this build reaches the network, so it is immune to interpreter
# registry or daemon proxy outages. The built image is referenced by its
# final content digest pinned in src/cernora_reference_workflow/spec_builder.py.
# See README.md in this directory for the exact context preparation commands.
FROM --platform=linux/arm64 python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

LABEL org.opencontainers.image.title="Cernora Reference pi runtime base" \
      org.opencontainers.image.licenses="Apache-2.0 AND MIT AND Python-2.0" \
      org.opencontainers.image.version="0.84.4" \
      org.opencontainers.image.source="https://registry.npmjs.org/@earendil-works/pi-coding-agent/-/pi-coding-agent-0.84.4.tgz"

COPY node-v22.23.2-linux-arm64.tar.gz /tmp/node.tar.gz
COPY package.json package-lock.json /tmp/pi-install/
COPY node_modules /tmp/pi-install/node_modules

RUN printf '%s  %s\n' \
        '013b59cfd2819703a6f4a14ab891fc46fc2a4e3f5bcd92de3fb4929b43e35b30' \
        '/tmp/node.tar.gz' \
        | sha256sum --check --strict \
    && tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.gz \
    && test "$(node --version)" = 'v22.23.2' \
    && mkdir -p /opt/pi \
    && mv /tmp/pi-install/node_modules /opt/pi/node_modules \
    && mv /tmp/pi-install/package.json /opt/pi/package.json \
    && mv /tmp/pi-install/package-lock.json /opt/pi/package-lock.json \
    && ln -s /opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/cli.js \
        /usr/local/bin/pi \
    && test "$(PI_OFFLINE=1 pi --version)" = '0.84.4'

ENV PI_OFFLINE=1 \
    PI_TELEMETRY=0 \
    PI_SKIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONUNBUFFERED=1
