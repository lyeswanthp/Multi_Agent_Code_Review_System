"""Tier 2: Context assembly — reads files ONCE, builds shared ReviewState."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from git import Repo

from code_review.ast_extractor import extract_focused_context
from code_review.models import ToolResults
from code_review.state import ReviewState
from code_review.tools.git_diff import get_overlap_diffs

logger = logging.getLogger(__name__)

# Import patterns (1-level deep, regex-based)
PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
)
JS_IMPORT_RE = re.compile(
    r"""(?:import\s+.*?\s+from\s+['"](.+?)['"]|require\s*\(\s*['"](.+?)['"]\s*\))""",
    re.MULTILINE,
)


def _resolve_py_import(module: str, repo_root: str) -> str | None:
    """Resolve a Python dotted module path to a file path within the repo."""
    rel = module.replace(".", os.sep)
    for suffix in [".py", "/__init__.py"]:
        candidate = os.path.join(repo_root, rel + suffix)
        if os.path.isfile(candidate):
            return os.path.relpath(candidate, repo_root).replace(os.sep, "/")
    return None


def _resolve_js_import(specifier: str, source_file: str, repo_root: str) -> str | None:
    """Resolve a JS/TS relative import to a file path within the repo."""
    if not specifier.startswith("."):
        return None  # skip node_modules
    source_dir = os.path.dirname(os.path.join(repo_root, source_file))
    base = os.path.normpath(os.path.join(source_dir, specifier))
    for suffix in ["", ".js", ".ts", ".jsx", ".tsx", "/index.js", "/index.ts"]:
        candidate = base + suffix
        if os.path.isfile(candidate):
            return os.path.relpath(candidate, repo_root).replace(os.sep, "/")
    return None


def _find_imports(filepath: str, content: str, repo_root: str) -> list[str]:
    """Extract 1-level-deep import targets from a file's content."""
    imports = []

    if filepath.endswith(".py"):
        for match in PY_IMPORT_RE.finditer(content):
            module = match.group(1) or match.group(2)
            resolved = _resolve_py_import(module, repo_root)
            if resolved:
                imports.append(resolved)

    elif filepath.endswith((".js", ".ts", ".jsx", ".tsx")):
        for match in JS_IMPORT_RE.finditer(content):
            specifier = match.group(1) or match.group(2)
            resolved = _resolve_js_import(specifier, filepath, repo_root)
            if resolved:
                imports.append(resolved)

    return imports


def assemble_context(
    path: str,
    tool_results: ToolResults,
    repo: Repo | None = None,
    commit_sha: str | None = None,
) -> ReviewState:
    """Build the shared ReviewState by reading each file exactly once.

    This is the ONLY place files are read. Agents never read files directly.
    """
    repo_root = path
    changed_files = sorted(tool_results.changed_files)
    overlap_files = sorted(tool_results.overlap_files)

    # Step 1: Read changed files ONCE
    file_contents: dict[str, str] = {}
    for filepath in changed_files:
        abs_path = os.path.join(repo_root, filepath)
        if os.path.isfile(abs_path):
            try:
                file_contents[filepath] = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning("Failed to read %s: %s", filepath, e)

    # Step 2: Resolve imports (1 level deep) from changed files
    import_context: dict[str, list[str]] = {}
    extra_files_to_read: set[str] = set()

    for filepath, content in file_contents.items():
        imports = _find_imports(filepath, content, repo_root)
        if imports:
            import_context[filepath] = imports
            extra_files_to_read.update(imports)

    # Step 3: Read imported files that aren't already in file_contents
    for filepath in extra_files_to_read - set(file_contents.keys()):
        abs_path = os.path.join(repo_root, filepath)
        if os.path.isfile(abs_path):
            try:
                file_contents[filepath] = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning("Failed to read import %s: %s", filepath, e)

    logger.info("Context assembled: %d files read (no duplicates)", len(file_contents))

    # Step 4: Build focused context — AST-extracted relevant blocks only
    raw_diff = tool_results.raw_diff
    focused_contents: dict[str, str] = {}
    for filepath, content in file_contents.items():
        focused = extract_focused_context(filepath, content, raw_diff)
        focused_contents[filepath] = focused
        if len(focused) < len(content):
            saved_pct = round((1 - len(focused) / len(content)) * 100)
            logger.debug("AST focus: %s reduced by %d%%", filepath, saved_pct)

    total_full = sum(len(c) for c in file_contents.values())
    total_focused = sum(len(c) for c in focused_contents.values())
    if total_full > 0:
        logger.info(
            "AST focus: %d chars → %d chars (%d%% reduction)",
            total_full, total_focused, round((1 - total_focused / total_full) * 100),
        )

    # Step 6: Get overlap diffs for git history agent
    overlap_diffs: dict[str, str] = {}
    if repo and commit_sha and overlap_files:
        overlap_diffs = get_overlap_diffs(repo, commit_sha, set(overlap_files))

    # Step 7: Serialize linter findings for agents
    linter_findings = [f.model_dump() for f in tool_results.ruff_findings + tool_results.eslint_findings]
    semgrep_findings = [f.model_dump() for f in tool_results.semgrep_findings]
    bandit_findings = [f.model_dump() for f in tool_results.bandit_findings]

    return ReviewState(
        raw_diff=raw_diff,
        changed_files=changed_files,
        overlap_files=overlap_files,
        file_contents=file_contents,
        focused_contents=focused_contents,
        import_context=import_context,
        linter_findings=linter_findings,
        semgrep_findings=semgrep_findings,
        bandit_findings=bandit_findings,
        overlap_diffs=overlap_diffs,
        findings=[],
        summary="",
    )
