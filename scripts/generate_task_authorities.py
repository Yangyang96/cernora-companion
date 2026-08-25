"""Generate canonical task-owned Test Plans and Profile fixture copies."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

from cernora_reference_workflow.common import canonical_json_bytes, sha256_file
from cernora_reference_workflow.test_runner import TestPlan

ROOT = Path(__file__).resolve().parents[1]
COMMAND = ("python", "/tests/run_tests.py", "--candidate-root", "/workspace")


class _TaskAuthorityConfiguration(TypedDict):
    authority_id: Literal["tiny-calculator-test-runner", "tiny-calculator-v2-test-runner"]
    test_ids: tuple[str, ...]
    profile_fixture: str


TASKS: dict[str, _TaskAuthorityConfiguration] = {
    "tiny-calculator-v1": {
        "authority_id": "tiny-calculator-test-runner",
        "test_ids": (
            "fail_to_pass_negative_left",
            "fail_to_pass_both_negative",
            "pass_to_pass_positive",
            "pass_to_pass_zero",
        ),
        "profile_fixture": "test-plan.json",
    },
    "tiny-calculator-v2": {
        "authority_id": "tiny-calculator-v2-test-runner",
        "test_ids": (
            "fail_to_pass_precedence",
            "fail_to_pass_parentheses",
            "fail_to_pass_unary_chain",
            "fail_to_pass_truncating_division",
            "fail_to_pass_whitespace",
            "fail_to_pass_add_overflow",
            "fail_to_pass_literal_overflow",
            "fail_to_pass_division_zero",
            "fail_to_pass_leading_zero",
            "pass_to_pass_literal",
            "pass_to_pass_simple_add",
            "pass_to_pass_negative_literal",
        ),
        "profile_fixture": "tiny-calculator-v2-test-plan.json",
    },
}


def build_task_plans() -> dict[str, TestPlan]:
    plans: dict[str, TestPlan] = {}
    for task_id, configuration in TASKS.items():
        task = ROOT / "tasks" / task_id
        plans[task_id] = TestPlan(
            schema_version="cernora.reference.test-plan/v1",
            authority_id=configuration["authority_id"],
            authority_version="1",
            command=COMMAND,
            working_directory="/workspace",
            test_ids=configuration["test_ids"],
            test_source_sha256=sha256_file(task / "tests/run_tests.py"),
            allowed_paths=("src/calc.py",),
            protected_paths=("pyproject.toml", "tests"),
        )
    return plans


def main() -> int:
    resources = ROOT / "profiles/cernora-reference-coding-v1/resources"
    for task_id, plan in build_task_plans().items():
        encoded = canonical_json_bytes(plan.model_dump(mode="json"))
        (ROOT / "tasks" / task_id / "tests/test-plan.json").write_bytes(encoded)
        fixture_name = TASKS[task_id]["profile_fixture"]
        (resources / fixture_name).write_bytes(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
