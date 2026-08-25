"""Generate the deterministic license inventory for every uv.lock package."""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

from cernora_reference_workflow.common import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]

# These locked distributions are platform-conditional on the development host.
# Values come from the exact wheel bytes and hashes recorded in uv.lock.
LOCKED_WHEEL_LICENSES = {
    ("colorama", "0.4.6"): "BSD-3-Clause",
    ("tzdata", "2026.3"): "Apache-2.0",
}


def _license(metadata: importlib.metadata.PackageMetadata) -> str:
    expression = metadata.get("License-Expression")
    if expression:
        return expression.strip()
    declared = metadata.get("License")
    if declared and declared.strip() and declared.strip().upper() != "UNKNOWN":
        return " ".join(declared.split())
    classifiers = [
        item.removeprefix("License :: ")
        for item in metadata.get_all("Classifier", [])
        if item.startswith("License :: ")
    ]
    return " | ".join(sorted(classifiers)) if classifiers else "not-declared-by-distribution"


def build_inventory() -> dict[str, object]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise RuntimeError("uv.lock does not contain a package list")
    installed = {
        distribution.metadata["Name"].lower().replace("_", "-"): distribution
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    records: list[dict[str, str]] = []
    for item in packages:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise RuntimeError("uv.lock package entry is malformed")
        name = item["name"]
        version = item.get("version")
        if name == "cernora-reference-workflow":
            continue
        if not isinstance(version, str):
            raise RuntimeError(f"locked package has no exact version: {name}")
        distribution = installed.get(name.lower().replace("_", "-"))
        if distribution is None:
            license_value = LOCKED_WHEEL_LICENSES.get((name, version), "")
            if not license_value:
                raise RuntimeError(f"locked package metadata is unavailable: {name}=={version}")
            metadata_status = "locked-wheel-metadata"
        else:
            if distribution.version != version:
                raise RuntimeError(f"installed version mismatch for {name}")
            license_value = _license(distribution.metadata)
            metadata_status = "installed-metadata"
        records.append(
            {
                "license": license_value,
                "metadata_status": metadata_status,
                "name": name,
                "version": version,
            }
        )
    records.sort(key=lambda item: item["name"])
    harbor = [item for item in records if item["name"] == "harbor"]
    if len(harbor) != 1 or harbor[0]["version"] != "0.16.1" or harbor[0]["license"] != "Apache-2.0":
        raise RuntimeError("Harbor 0.16.1 Apache-2.0 license record is missing")
    return {
        "schema_version": "cernora.reference.license-inventory/v1",
        "lockfile": "uv.lock",
        "packages": records,
    }


def main() -> int:
    payload = build_inventory()
    (ROOT / "docs/license-inventory.json").write_bytes(canonical_json_bytes(payload) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
