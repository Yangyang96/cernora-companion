#!/usr/bin/env python3
"""Create or verify the closed development-only Agent pilot request bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cernora_reference_workflow.common import ContractError, canonical_json_bytes
from cernora_reference_workflow.development_pilot_bundle import (
    DevelopmentPilotBundleManifest,
    create_development_pilot_bundle,
    verify_development_pilot_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--repository-root", type=Path, required=True)
    create.add_argument("--corpus-root", type=Path, required=True)
    create.add_argument("--image-authorities", type=Path, required=True)
    create.add_argument("--companion-wheel", type=Path, required=True)
    create.add_argument("--companion-version", required=True)
    create.add_argument("--cernora-wheel", type=Path, required=True)
    create.add_argument("--cernora-version", required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--companion-wheel", type=Path, required=True)
    verify.add_argument("--cernora-wheel", type=Path, required=True)
    return parser


def _summary(manifest: DevelopmentPilotBundleManifest) -> bytes:
    return canonical_json_bytes(
        {"bundle_id": manifest.bundle_id, "plan_id": manifest.plan_id, "status": manifest.status}
    )


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "create":
            manifest = create_development_pilot_bundle(
                arguments.output,
                corpus_root=arguments.corpus_root,
                image_authorities=arguments.image_authorities,
                companion_wheel=arguments.companion_wheel,
                cernora_wheel=arguments.cernora_wheel,
                companion_version=arguments.companion_version,
                cernora_version=arguments.cernora_version,
                repository_root=arguments.repository_root,
            )
        else:
            manifest = verify_development_pilot_bundle(
                arguments.bundle,
                companion_wheel=arguments.companion_wheel,
                cernora_wheel=arguments.cernora_wheel,
            )
    except (ContractError, OSError, ValueError):
        print("error: development pilot bundle operation failed", file=sys.stderr)
        return 1
    print(_summary(manifest).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
