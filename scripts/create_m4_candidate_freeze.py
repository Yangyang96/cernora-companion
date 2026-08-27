#!/usr/bin/env python3
"""Create the deterministic M4 development pilot and CandidateFreeze."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.development_pilot import create_candidate_freeze


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visible-root", type=Path, required=True)
    parser.add_argument("--public-manifest", type=Path, required=True)
    parser.add_argument("--pilot-output", type=Path, required=True)
    parser.add_argument("--freeze-output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        pilot, freeze = create_candidate_freeze(
            visible_root=arguments.visible_root,
            public_manifest=arguments.public_manifest,
            pilot_output=arguments.pilot_output,
            freeze_output=arguments.freeze_output,
        )
    except (ContractError, OSError, ValueError):
        print("error: CandidateFreeze generation failed", file=sys.stderr)
        return 1
    print(
        canonical_json_bytes(
            {
                "candidate_freeze_id": freeze.candidate_freeze_id,
                "candidate_freeze_sha256": freeze.candidate_freeze_sha256,
                "pilot_id": freeze.pilot.pilot_id,
                "summary_id": pilot.summary.summary_id,
                "summary_sha256": pilot.summary.summary_sha256,
            }
        ).decode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
