"""Dependency-free deterministic test authority for tiny-calculator-v1."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

TESTS: tuple[tuple[str, str, int, int, int], ...] = (
    ("fail_to_pass_negative_left", "fail-to-pass", -2, 1, -1),
    ("fail_to_pass_both_negative", "fail-to-pass", -2, -3, -5),
    ("pass_to_pass_positive", "pass-to-pass", 2, 3, 5),
    ("pass_to_pass_zero", "pass-to-pass", 0, 4, 4),
)


def load_candidate(root: Path) -> ModuleType:
    source = root / "src/calc.py"
    spec = importlib.util.spec_from_file_location("tiny_candidate_calc", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(candidate_root: Path) -> dict[str, object]:
    try:
        module = load_candidate(candidate_root)
        add = module.add
        if not callable(add):
            raise TypeError("candidate add is not callable")
        function: Callable[[int, int], object] = add
    except Exception as exc:
        return {
            "schema_version": "cernora.reference.test-results/v1",
            "termination": "runner-error",
            "tests": [],
            "runner_error": type(exc).__name__,
        }

    results: list[dict[str, object]] = []
    for test_id, category, left, right, expected in TESTS:
        try:
            actual = function(left, right)
            passed = type(actual) is int and actual == expected
            error = None
        except Exception as exc:
            actual = None
            passed = False
            error = type(exc).__name__
        results.append(
            {
                "test_id": test_id,
                "category": category,
                "passed": passed,
                "expected": expected,
                "actual": actual,
                "error": error,
            }
        )
    return {
        "schema_version": "cernora.reference.test-results/v1",
        "termination": "exited",
        "tests": results,
        "runner_error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.candidate_root)
    sys.stdout.write(json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n")
    tests = result["tests"]
    passed = isinstance(tests, list) and len(tests) == 4 and all(t["passed"] for t in tests)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
