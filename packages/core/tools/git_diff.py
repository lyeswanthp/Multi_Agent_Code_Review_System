"""Git diff utilities — changed files, overlap detection, raw diffs."""

from __future__ import annotations

import logging

from git import Repo

logger = logging.getLogger(__name__)

EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def get_changed_files(repo: Repo, commit_sha: str) -> set[str]:
    """Get the set of file paths changed in a specific commit."""
    commit = repo.commit(commit_sha)
    if not commit.parents:
        diff = commit.diff(EMPTY_TREE_SHA)
    else:
        diff = commit.diff(commit.parents[0])
    paths = set()
    for d in diff:
        if d.a_path:
            paths.add(d.a_path)
        if d.b_path:
            paths.add(d.b_path)
    return paths


def get_file_overlap(repo: Repo, current_sha: str) -> set[str]:
    """Get files changed in BOTH current and previous commit (intersection)."""
    commit = repo.commit(current_sha)
    if not commit.parents:
        return set()

    previous_sha = str(commit.parents[0])
    current_files = get_changed_files(repo, current_sha)
    previous_files = get_changed_files(repo, previous_sha)
    return current_files & previous_files


def get_diff(repo: Repo, commit_sha: str) -> str:
    """Get the raw unified diff for a commit."""
    commit = repo.commit(commit_sha)
    if not commit.parents:
        return repo.git.diff(EMPTY_TREE_SHA, commit_sha)
    return repo.git.diff(str(commit.parents[0]), commit_sha)


def get_uncommitted_files(repo: Repo) -> set[str]:
    """Get files with uncommitted changes: staged + modified + untracked."""
    paths: set[str] = set()

    # Modified (unstaged)
    for d in repo.index.diff(None):
        if d.a_path:
            paths.add(d.a_path)
        if d.b_path:
            paths.add(d.b_path)

    # Staged
    for d in repo.index.diff("HEAD"):
        if d.a_path:
            paths.add(d.a_path)
        if d.b_path:
            paths.add(d.b_path)

    # Untracked
    paths.update(repo.untracked_files)

    # Only keep source files
    _src_exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".php"}
    paths = {p for p in paths if any(p.endswith(ext) for ext in _src_exts)}

    logger.info("Uncommitted changes: %d files", len(paths))
    return paths


def get_uncommitted_diff(repo: Repo) -> str:
    """Get unified diff of all uncommitted changes (staged + unstaged)."""
    parts = []
    # Staged diff
    staged = repo.git.diff("--cached")
    if staged:
        parts.append(staged)
    # Unstaged diff
    unstaged = repo.git.diff()
    if unstaged:
        parts.append(unstaged)
    return "\n".join(parts)


def get_overlap_diffs(repo: Repo, current_sha: str, overlap_files: set[str]) -> dict[str, str]:
    """Get per-file diffs between previous and current commit for overlap files only."""
    commit = repo.commit(current_sha)
    if not commit.parents or not overlap_files:
        return {}

    previous_sha = str(commit.parents[0])
    diffs = {}
    for path in overlap_files:
        try:
            diffs[path] = repo.git.diff(previous_sha, current_sha, "--", path)
        except Exception as e:
            logger.warning("Failed to get diff for %s: %s", path, e)
    return diffs
