"""Parallel tool runner — executes all Tier 1 tools concurrently."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from code_review.models import Finding, ToolResults
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
    get_uncommitted_diff,
    get_uncommitted_files,
)

logger = logging.getLogger(__name__)

# File extensions to include in a full directory scan (no-git fallback)
_SCAN_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx"}
_JS_EXTENSIONS = {".js", ".ts", ".jsx", ".tsx"}
# Directories to skip during scan
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", "dist", "build"}


def _has_js_files(path: str) -> bool:
    root = Path(path)
    if root.is_file():
        return root.suffix in _JS_EXTENSIONS
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if any(Path(f).suffix in _JS_EXTENSIONS for f in filenames):
            return True
    return False


def _scan_all_files(path: str) -> set[str]:
    """Walk the directory and return relative paths of all source files."""
    found: set[str] = set()
    root = Path(path).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if Path(filename).suffix in _SCAN_EXTENSIONS:
                abs_path = Path(dirpath) / filename
                rel = abs_path.relative_to(root).as_posix()
                found.add(rel)
    logger.info("Full scan found %d source files in %s", len(found), path)
    return found


def _filter_findings_to_files(findings: list[Finding], changed_files: set[str],
                               repo_root: str) -> list[Finding]:
    """Keep only findings whose file path matches a changed file."""
    if not changed_files:
        return findings

    # Normalise changed_files to absolute paths for comparison
    abs_changed = set()
    root = Path(repo_root).resolve()
    for f in changed_files:
        abs_changed.add(str((root / f).resolve()))
        abs_changed.add(f)  # also keep relative form

    filtered = []
    for finding in findings:
        # Normalise the finding's file path
        fpath = Path(finding.file).resolve() if finding.file else None
        if fpath and (str(fpath) in abs_changed or finding.file in changed_files):
            filtered.append(finding)
    return filtered


async def run_all_tools(
    path: str,
    repo: Repo | None = None,
    commit_sha: str | None = None,
) -> ToolResults:
    """Run all Tier 1 tools in parallel and return aggregated results."""

    from code_review.events import bus

    has_js = _has_js_files(path)
    tool_names = ["ruff", "semgrep", "bandit"] + (["eslint"] if has_js else [])
    for t in tool_names:
        bus.emit("tool.start", tool=t)
    if not has_js:
        bus.emit("tool.skip", tool="eslint", reason="no JS/TS files")

    # Determine changed files FIRST — so we can scope tool runs
    changed_files: set[str] = set()
    overlap_files: set[str] = set()
    raw_diff = ""
    scan_mode = "full"

    if repo and commit_sha:
        # Explicit commit review
        changed_files = get_changed_files(repo, commit_sha)
        overlap_files = get_file_overlap(repo, commit_sha)
        raw_diff = get_diff(repo, commit_sha)
        scan_mode = "commit"
        bus.emit("log.warning", logger="runner",
                 message=f"Reviewing commit {commit_sha[:8]}: {len(changed_files)} changed files")
    elif repo:
        # Auto-detect uncommitted changes
        changed_files = get_uncommitted_files(repo)
        if changed_files:
            raw_diff = get_uncommitted_diff(repo)
            scan_mode = "uncommitted"
            bus.emit("log.warning", logger="runner",
                     message=f"Uncommitted changes detected: {len(changed_files)} files")
        else:
            logger.info("No uncommitted changes; falling back to full scan")
            changed_files = _scan_all_files(path)
            scan_mode = "full"
    else:
        # No git context at all
        logger.info("No git repo; scanning all source files in %s", path)
        changed_files = _scan_all_files(path)

    # Build list of individual file paths to pass to tools (only changed files)
    repo_root = Path(path).resolve()
    if scan_mode in ("uncommitted", "commit"):
        # Run tools on specific files only
        target_files = [str(repo_root / f) for f in changed_files if (repo_root / f).is_file()]
        tool_path = target_files if target_files else path
    else:
        tool_path = path

    # Run linters concurrently
    if isinstance(tool_path, list):
        # Run tools on each file individually and merge
        ruff_task = asyncio.create_task(_run_on_files(run_ruff, tool_path))
        semgrep_task = asyncio.create_task(_run_on_files(run_semgrep, tool_path))
        bandit_task = asyncio.create_task(_run_on_files(run_bandit, tool_path))
        eslint_task = asyncio.create_task(_run_on_files(run_eslint, tool_path)) if has_js else None
    else:
        ruff_task = asyncio.create_task(run_ruff(tool_path))
        semgrep_task = asyncio.create_task(run_semgrep(tool_path))
        bandit_task = asyncio.create_task(run_bandit(tool_path))
        eslint_task = asyncio.create_task(run_eslint(tool_path)) if has_js else None

    tasks = [ruff_task, semgrep_task, bandit_task]
    if eslint_task:
        tasks.append(eslint_task)

    results = await asyncio.gather(*tasks)
    ruff_findings, semgrep_findings, bandit_findings = results[0], results[1], results[2]
    eslint_findings = results[3] if eslint_task else []

    for tool, findings in [("ruff", ruff_findings), ("semgrep", semgrep_findings),
                            ("bandit", bandit_findings), ("eslint", eslint_findings)]:
        if tool == "eslint" and not has_js:
            continue
        bus.emit("tool.done", tool=tool, findings=len(findings))

    return ToolResults(
        ruff_findings=ruff_findings,
        semgrep_findings=semgrep_findings,
        bandit_findings=bandit_findings,
        eslint_findings=eslint_findings,
        changed_files=changed_files,
        overlap_files=overlap_files,
        raw_diff=raw_diff,
    )


# Cap concurrent subprocess calls — avoids overwhelming the system on large repos.
_SEMAPHORE = asyncio.Semaphore(8)


async def _run_on_files(tool_fn, file_paths: list[str]) -> list[Finding]:
    """Run a tool on multiple individual files and merge results."""
    if not file_paths:
        return []

    async def _run_one(fp: str) -> list[Finding]:
        async with _SEMAPHORE:
            return await tool_fn(fp)

    results = await asyncio.gather(*[_run_one(fp) for fp in file_paths])
    merged: list[Finding] = []
    for r in results:
        merged.extend(r)
    return merged
