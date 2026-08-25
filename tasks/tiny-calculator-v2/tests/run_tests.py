"""Dependency-free deterministic test authority for tiny-calculator-v2."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

TESTS: tuple[tuple[str, str, str, int, bool], ...] = (
    ("fail_to_pass_precedence", "fail-to-pass", "2+3*4", 14, False),
    ("fail_to_pass_parentheses", "fail-to-pass", "(2+3)*4", 20, False),
    ("fail_to_pass_unary_chain", "fail-to-pass", "--5+-2", 3, False),
    ("fail_to_pass_truncating_division", "fail-to-pass", "-7//3", -2, False),
    ("fail_to_pass_whitespace", "fail-to-pass", " 2 * ( -3 + 5 ) ", 4, False),
    ("fail_to_pass_add_overflow", "fail-to-pass", "2147483647+1", 1, True),
    ("fail_to_pass_literal_overflow", "fail-to-pass", "2147483648", 1, True),
    ("fail_to_pass_division_zero", "fail-to-pass", "9//0", 1, True),
    ("fail_to_pass_leading_zero", "fail-to-pass", "01", 1, True),
    ("pass_to_pass_literal", "pass-to-pass", "7", 7, False),
    ("pass_to_pass_simple_add", "pass-to-pass", "2+3", 5, False),
    ("pass_to_pass_negative_literal", "pass-to-pass", "-4", -4, False),
)


def load_candidate(root: Path) -> ModuleType:
    source = root / "src/calc.py"
    spec = importlib.util.spec_from_file_location("tiny_candidate_calc_v2", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _observe(function: Callable[[str], object], expression: str, expect_error: bool) -> object:
    if not expect_error:
        return function(expression)
    try:
        function(expression)
    except ValueError:
        return 1
    return 0


def run(candidate_root: Path) -> dict[str, object]:
    try:
        module = load_candidate(candidate_root)
        evaluate = module.evaluate
        if not callable(evaluate):
            raise TypeError("candidate evaluate is not callable")
        function: Callable[[str], object] = evaluate
    except Exception as exc:
        return {
            "schema_version": "cernora.reference.test-results/v1",
            "termination": "runner-error",
            "tests": [],
            "runner_error": type(exc).__name__,
        }

    results: list[dict[str, object]] = []
    for test_id, category, expression, expected, expect_error in TESTS:
        try:
            actual = _observe(function, expression, expect_error)
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
    passed = (
        isinstance(tests, list)
        and len(tests) == len(TESTS)
        and all(item["passed"] for item in tests)
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
