#!/usr/bin/env python3
"""Create or verify a non-authoritative offline Controlled Study preparation bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.study_preparation import (
    StudyPreparationManifest,
    create_study_preparation_bundle,
    verify_study_preparation_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create one new closed preparation")
    create.add_argument("--companion-wheel", type=Path, required=True)
    create.add_argument("--companion-version", required=True)
    create.add_argument("--cernora-wheel", type=Path, required=True)
    create.add_argument("--cernora-version", required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="strictly reload one preparation")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--companion-wheel", type=Path, required=True)
    verify.add_argument("--cernora-wheel", type=Path, required=True)
    return parser


def _summary(manifest: StudyPreparationManifest) -> bytes:
    return canonical_json_bytes(
        {
            "preparation_id": manifest.preparation_id,
            "status": manifest.status,
        }
    )


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "create":
            manifest = create_study_preparation_bundle(
                arguments.output,
                companion_wheel=arguments.companion_wheel,
                cernora_wheel=arguments.cernora_wheel,
                companion_version=arguments.companion_version,
                cernora_version=arguments.cernora_version,
            )
        else:
            manifest = verify_study_preparation_bundle(
                arguments.bundle,
                companion_wheel=arguments.companion_wheel,
                cernora_wheel=arguments.cernora_wheel,
            )
    except (ContractError, OSError, ValueError):
        print("error: Study preparation bundle operation failed", file=sys.stderr)
        return 1
    print(_summary(manifest).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
