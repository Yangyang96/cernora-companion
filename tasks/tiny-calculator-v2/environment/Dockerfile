# The local tag must resolve to the Runtime digest pinned by the ExperimentSpec.
FROM --platform=linux/arm64 cernora-reference/pi-runtime:0.84.4

WORKDIR /workspace
COPY pyproject.toml /workspace/pyproject.toml
COPY src /workspace/src
