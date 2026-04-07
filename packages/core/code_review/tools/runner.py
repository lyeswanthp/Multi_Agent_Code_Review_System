"""Parallel tool runner — executes all Tier 1 tools concurrently."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from code_review.models import ToolResults
from code_review.tools.bandit_runner import run_bandit
from code_review.tools.eslint_runner import run_eslint
from code_review.tools.ruff_runner import run_ruff
from code_review.tools.semgrep_runner import run_semgrep

if TYPE_CHECKING:
    from git import Repo

from code_review.tools.git_diff import (
    get_changed_files,
    get_diff,
    get_file_overlap,
    get_overlap_diffs,
)

logger = logging.getLogger(__name__)


async def run_all_tools(
    path: str,
    repo: Repo | None = None,
    commit_sha: str | None = None,
) -> ToolResults:
    """Run all Tier 1 tools in parallel and return aggregated results."""

    # Run linters concurrently
    ruff_task = asyncio.create_task(run_ruff(path))
    semgrep_task = asyncio.create_task(run_semgrep(path))
    bandit_task = asyncio.create_task(run_bandit(path))
    eslint_task = asyncio.create_task(run_eslint(path))

    ruff_findings, semgrep_findings, bandit_findings, eslint_findings = await asyncio.gather(
        ruff_task, semgrep_task, bandit_task, eslint_task,
    )

    # Git operations (synchronous, but fast)
    changed_files: set[str] = set()
    overlap_files: set[str] = set()
    raw_diff = ""

    if repo and commit_sha:
        changed_files = get_changed_files(repo, commit_sha)
        overlap_files = get_file_overlap(repo, commit_sha)
        raw_diff = get_diff(repo, commit_sha)

    return ToolResults(
        ruff_findings=ruff_findings,
        semgrep_findings=semgrep_findings,
        bandit_findings=bandit_findings,
        eslint_findings=eslint_findings,
        changed_files=changed_files,
        overlap_files=overlap_files,
        raw_diff=raw_diff,
    )
