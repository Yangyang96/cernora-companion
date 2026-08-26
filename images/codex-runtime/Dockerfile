FROM --platform=linux/arm64 python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

LABEL org.opencontainers.image.title="Cernora Priority 3 Codex runtime base" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="0.148.0" \
      org.opencontainers.image.source="https://registry.npmjs.org/@openai/codex/-/codex-0.148.0-linux-arm64.tgz"

# The platform package is the exact Apache-2.0 npm artifact. The archive is verified
# before extraction; the installed native tools are then checked independently.
ADD https://registry.npmjs.org/@openai/codex/-/codex-0.148.0-linux-arm64.tgz \
    /tmp/codex-0.148.0-linux-arm64.tgz

RUN printf '%s  %s\n' \
        'feba463f31f8cda589192d5d3339359c5b35bc1366e36909a62d4e488a6822e5' \
        '/tmp/codex-0.148.0-linux-arm64.tgz' \
        | sha256sum --check --strict \
    && mkdir -p /opt/codex \
    && tar -xzf /tmp/codex-0.148.0-linux-arm64.tgz \
        -C /opt/codex --strip-components=3 \
        package/vendor/aarch64-unknown-linux-musl \
    && rm /tmp/codex-0.148.0-linux-arm64.tgz \
    && printf '%s  %s\n' \
        '7515d0b61e723374c68d4acdcb8815e378f84d088b0c50638f27d1094bffe536' \
        '/opt/codex/bin/codex' \
        'e36d0eb52e70696bdf1781392722e05a21bb91d3b7b762ef5ec20e5df2ec687b' \
        '/opt/codex/codex-path/rg' \
        | sha256sum --check --strict \
    && ln -s /opt/codex/bin/codex /usr/local/bin/codex \
    && ln -s /opt/codex/codex-path/rg /usr/local/bin/rg \
    && test "$(codex --version)" = 'codex-cli 0.148.0'

ENV PATH="/opt/codex/codex-path:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONUNBUFFERED=1
