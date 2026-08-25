"""Regenerate the canonical tiny-calculator ExperimentSpec examples."""

from __future__ import annotations

from pathlib import Path

from cernora_reference_workflow.spec_builder import (
    TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
    build_tiny_calculator_spec,
    build_tiny_calculator_v2_spec,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    examples = ROOT / "examples"
    examples.mkdir(exist_ok=True)
    (examples / "tiny-calculator-v1.json").write_bytes(
        build_tiny_calculator_spec(ROOT).canonical_bytes()
    )
    (examples / "tiny-calculator-v1-timeout.json").write_bytes(
        build_tiny_calculator_spec(
            ROOT,
            timeout_seconds=3,
            agent_timeout_multiplier=TIMEOUT_AGENT_TIMEOUT_MULTIPLIER,
        ).canonical_bytes()
    )
    (examples / "tiny-calculator-v1-interruption.json").write_bytes(
        build_tiny_calculator_spec(ROOT, operator_interrupt=True).canonical_bytes()
    )
    (examples / "tiny-calculator-v2.json").write_bytes(
        build_tiny_calculator_v2_spec(ROOT).canonical_bytes()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
