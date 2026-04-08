"""Parallel tool runner — executes all Tier 1 tools concurrently."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
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

# File extensions to include in a full directory scan (no-git fallback)
_SCAN_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx"}
# Directories to skip during scan
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", "dist", "build"}


def _scan_all_files(path: str) -> set[str]:
    """Walk the directory and return relative paths of all source files."""
    found: set[str] = set()
    root = Path(path).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune unwanted directories in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if Path(filename).suffix in _SCAN_EXTENSIONS:
                abs_path = Path(dirpath) / filename
                rel = abs_path.relative_to(root).as_posix()
                found.add(rel)
    logger.info("Full scan found %d source files in %s", len(found), path)
    return found


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
    else:
        # No git context — fall back to scanning all source files in the directory
        logger.info("No git context provided; scanning all source files in %s", path)
        changed_files = _scan_all_files(path)

    return ToolResults(
        ruff_findings=ruff_findings,
        semgrep_findings=semgrep_findings,
        bandit_findings=bandit_findings,
        eslint_findings=eslint_findings,
        changed_files=changed_files,
        overlap_files=overlap_files,
        raw_diff=raw_diff,
    )
