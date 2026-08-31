#!/usr/bin/env python3
"""Build the fresh development-pilot task images twice without network access."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from create_m4_task_images import (
    ACCEPTED_M4_RUNTIME_BASE,
    _build_all_images,
    _exclusive_publish,
    _inside_git_worktree,
    _isolated_docker_environment,
)

from cernora_reference_workflow.common import ContractError, read_regular_file_bytes
from cernora_reference_workflow.development_agent_pilot import (
    load_development_pilot_corpus,
    materialize_development_pilot_image_set,
)

_IMAGE_PREFIX = "cernora-reference/p4-pilot-"


def create_development_pilot_images(
    *,
    corpus_root: Path,
    build_base_image: str,
    work_root: Path,
    output: Path,
) -> None:
    """Publish exact reproducible image identities for the visible six-Case corpus."""

    corpus = load_development_pilot_corpus(corpus_root)
    if not work_root.is_dir() or work_root.is_symlink() or _inside_git_worktree(work_root):
        raise ContractError("development image work root must be one Git-external real directory")
    if output.exists() or output.is_symlink():
        raise ContractError("development image authority output must be new")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ContractError("development image authority output parent must be one real directory")
    if build_base_image != ACCEPTED_M4_RUNTIME_BASE:
        raise ContractError("development image build requires the accepted Runtime base authority")
    with _isolated_docker_environment(work_root) as environment:
        images = _build_all_images(
            corpus.tasks,
            build_base_image=build_base_image,
            work_root=work_root,
            environment=environment,
            image_prefix=_IMAGE_PREFIX,
        )
    authority = materialize_development_pilot_image_set(
        build_base_image=build_base_image,
        images=images,
    )
    _exclusive_publish(output, authority.canonical_bytes())
    if read_regular_file_bytes(output) != authority.canonical_bytes():
        raise ContractError("published development image authorities changed after publication")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--build-base-image", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        create_development_pilot_images(
            corpus_root=arguments.corpus_root,
            build_base_image=arguments.build_base_image,
            work_root=arguments.work_root,
            output=arguments.output,
        )
    except (ContractError, OSError, ValueError):
        print("error: development pilot image creation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
