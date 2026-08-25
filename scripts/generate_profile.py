"""Generate the explicit companion Profile authority document."""

from __future__ import annotations

from pathlib import Path

from cernora_reference_workflow.common import canonical_json_bytes
from cernora_reference_workflow.profile import create_profile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    authority = create_profile().authority
    destination = ROOT / "profiles/cernora-reference-coding-v1/profile.json"
    destination.write_bytes(
        canonical_json_bytes(authority.model_dump(mode="json", exclude_none=False))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
