# The local tag must resolve to the Runtime digest pinned by the ExperimentSpec.
FROM --platform=linux/arm64 cernora-reference/codex-runtime:0.148.0

WORKDIR /workspace
COPY pyproject.toml /workspace/pyproject.toml
COPY src /workspace/src
