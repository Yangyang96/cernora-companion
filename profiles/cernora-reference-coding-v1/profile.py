"""Explicit local entry point for the companion-owned coding Profile."""

from cernora import Profile

from cernora_reference_workflow.profile import ReferenceCodingProfile


def create_profile() -> Profile:
    return ReferenceCodingProfile()
