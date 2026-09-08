"""Generate the closed failure matrix from a frozen successful export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cernora_reference_workflow.experiment_spec import ExperimentSpec
from tests.support.derived_matrix import generate_derived_matrix


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = generate_derived_matrix(
        source_export=args.export,
        source_spec=ExperimentSpec.from_file(args.spec),
        destination=args.output,
    )
    print(
        json.dumps(
            {
                "matrix_id": manifest.matrix_id,
                "output": args.output.as_posix(),
                "status": "published",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
