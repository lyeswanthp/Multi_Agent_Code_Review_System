"""Tier 2: Context assembly — reads files ONCE, builds shared ReviewState.

Two modes:
  - diff mode (uncommitted/commit): sends only changed hunks + surrounding context
  - full mode (no git): sends full file contents (fallback)
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from git import Repo

from code_review.models import ToolResults
from code_review.state import ReviewState
from code_review.tools.git_diff import get_overlap_diffs

logger = logging.getLogger(__name__)

# Context lines around each diff hunk sent to agents
_HUNK_CONTEXT_LINES = 8

# Unified diff hunk header: @@ -start,count +start,count @@
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def _extract_changed_lines(raw_diff: str) -> dict[str, set[int]]:
    """Parse unified diff to find which lines changed in each file.

    Returns {filepath: {line_numbers}} — only actual added/modified lines from
    the '+' side of the diff (not unchanged context within hunks).
    """
    file_lines: dict[str, set[int]] = {}
    current_file = None
    current_line = 0  # tracks position in the new file within a hunk

    for line in raw_diff.splitlines():
        # New file header: +++ b/path/to/file.py
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in file_lines:
                file_lines[current_file] = set()
            current_line = 0
        elif line.startswith("+++ "):
            # Handle +++ paths without b/ prefix
            current_file = line[4:]
            if current_file not in file_lines:
                file_lines[current_file] = set()
            current_line = 0

        # Hunk header — reset line counter to hunk start
        elif current_file and line.startswith("@@"):
            match = _HUNK_RE.match(line)
            if match:
                current_line = int(match.group(1))

        # Inside a hunk: track actual additions
        elif current_file and current_line > 0:
            if line.startswith("+"):
                # This is an added/modified line
                file_lines[current_file].add(current_line)
                current_line += 1
            elif line.startswith("-"):
                # Deleted line — doesn't exist in new file, don't advance
                pass
            else:
                # Context line (unchanged) — advance but don't mark
                current_line += 1

    return file_lines


def _extract_diff_hunks(raw_diff: str) -> dict[str, dict[str, str]]:
    """Parse unified diff into per-file old/new code sections.

    Returns {filepath: {"old": removed lines, "new": added lines, "diff": unified hunk view}}.
    """
    result: dict[str, dict[str, str]] = {}
    current_file = None
    old_lines: list[str] = []
    new_lines: list[str] = []
    diff_lines: list[str] = []

    def _flush():
        if current_file and (old_lines or new_lines):
            result[current_file] = {
                "old": "\n".join(old_lines),
                "new": "\n".join(new_lines),
                "diff": "\n".join(diff_lines),
            }

    for line in raw_diff.splitlines():
        if line.startswith("+++ b/") or (line.startswith("+++ ") and not line.startswith("+++ b/")):
            _flush()
            current_file = line[6:] if line.startswith("+++ b/") else line[4:]
            old_lines, new_lines, diff_lines = [], [], []
        elif line.startswith("--- "):
            continue  # skip old file header
        elif line.startswith("@@") and current_file:
            diff_lines.append(line)
        elif current_file:
            if line.startswith("+"):
                new_lines.append(line[1:])  # strip the +
                diff_lines.append(line)
            elif line.startswith("-"):
                old_lines.append(line[1:])  # strip the -
                diff_lines.append(line)
            elif line.startswith(" ") or line == "":
                # context line
                diff_lines.append(line)

    _flush()
    return result


def _extract_hunks(content: str, changed_lines: set[int], context: int = _HUNK_CONTEXT_LINES) -> str:
    """Extract only the changed hunks from file content with surrounding context.

    Returns a compact string with line numbers, showing only the changed regions.
    """
    if not changed_lines:
        # New file — no diff info available, return first 60 lines
        lines = content.splitlines()
        return "\n".join(f"{i+1:4d} | {l}" for i, l in enumerate(lines[:60]))

    lines = content.splitlines()
    total = len(lines)

    # Build set of lines to include (changed + context around each)
    include: set[int] = set()
    for ln in changed_lines:
        for i in range(max(1, ln - context), min(total + 1, ln + context + 1)):
            include.add(i)

    # Build output with separator for gaps
    sorted_lines = sorted(include)
    parts: list[str] = []
    prev = -1

    for ln in sorted_lines:
        if ln < 1 or ln > total:
            continue
        if prev != -1 and ln > prev + 1:
            parts.append("    ...")  # gap marker
        marker = ">>>" if ln in changed_lines else "   "
        parts.append(f"{ln:4d} {marker} {lines[ln - 1]}")
        prev = ln

    return "\n".join(parts)


def assemble_context(
    path: str,
    tool_results: ToolResults,
    repo: Repo | None = None,
    commit_sha: str | None = None,
) -> ReviewState:
    """Build the shared ReviewState.

    In diff mode: only reads changed files and extracts changed hunks.
    In full mode: reads all source files (fallback when no git).
    """
    from code_review.events import bus

    repo_root = path
    changed_files = sorted(tool_results.changed_files)
    overlap_files = sorted(tool_results.overlap_files)
    raw_diff = tool_results.raw_diff
    has_diff = bool(raw_diff.strip())

    # Parse diff to find changed line numbers and old/new code per file
    changed_line_map = _extract_changed_lines(raw_diff) if has_diff else {}
    diff_context = _extract_diff_hunks(raw_diff) if has_diff else {}

    # Step 1: Read changed files ONCE
    file_contents: dict[str, str] = {}
    for filepath in changed_files:
        abs_path = os.path.join(repo_root, filepath)
        if os.path.isfile(abs_path):
            try:
                content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
                file_contents[filepath] = content
                bus.emit("file.loaded", path=filepath, chars=len(content))
            except Exception as e:
                logger.warning("Failed to read %s: %s", filepath, e)

    logger.info("Context assembled: %d files read", len(file_contents))

    # Step 2: Build focused contents — diff hunks only (not full files)
    focused_contents: dict[str, str] = {}
    for filepath, content in file_contents.items():
        # Find matching diff path (may differ by prefix)
        diff_lines = changed_line_map.get(filepath, set())
        if not diff_lines:
            # Try to match by filename only (diff paths may use forward slashes)
            norm = filepath.replace("\\", "/")
            for diff_path, lines in changed_line_map.items():
                if diff_path.replace("\\", "/").endswith(norm) or norm.endswith(diff_path.replace("\\", "/")):
                    diff_lines = lines
                    break

        if has_diff:
            hunk_text = _extract_hunks(content, diff_lines)
            focused_contents[filepath] = hunk_text
            if diff_lines:
                bus.emit("file.loaded", path=f"{filepath} (hunks)",
                         chars=len(hunk_text))
        else:
            # No diff — send first 80 lines as a reasonable preview
            lines = content.splitlines()[:80]
            focused_contents[filepath] = "\n".join(f"{i+1:4d} | {l}" for i, l in enumerate(lines))

    # Emit old/new code info for dashboard
    for filepath, dc in diff_context.items():
        bus.emit("diff.file", path=filepath,
                 old_lines=dc["old"].count("\n") + (1 if dc["old"] else 0),
                 new_lines=dc["new"].count("\n") + (1 if dc["new"] else 0),
                 old_chars=len(dc["old"]),
                 new_chars=len(dc["new"]))

    total_full = sum(len(c) for c in file_contents.values())
    total_focused = sum(len(c) for c in focused_contents.values())
    if total_full > 0:
        reduction = round((1 - total_focused / total_full) * 100)
        logger.info("Context: %d chars → %d chars (%d%% reduction)", total_full, total_focused, reduction)
        bus.emit("log.warning", logger="context",
                 message=f"Diff hunks: {total_full} → {total_focused} chars ({reduction}% reduction)")

    # Step 3 & 4: Global Knowledge Graph & Topological RAG
    from code_review.tools.runner import scan_all_files
    from code_review.skeleton import extract_skeleton
    
    external_skeletons: dict[str, str] = {}
    graph_context: dict = {"nodes": [], "edges": []}
    call_chain_text: str = ""
    
    if file_contents:
        all_files = scan_all_files(repo_root)
        all_file_contents: dict[str, str] = {}
        
        # Read the entire codebase to build the global graph
        for filepath in all_files:
            if filepath in file_contents:
                all_file_contents[filepath] = file_contents[filepath]
            else:
                abs_path = os.path.join(repo_root, filepath)
                if os.path.isfile(abs_path):
                    try:
                        all_file_contents[filepath] = Path(abs_path).read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass
        try:
            from code_review.knowledge_graph import (
                build_knowledge_graph,
                get_affected_subgraph,
                get_call_chain_context
            )
            # 1. Build Repo-Scope Graph
            kg = build_knowledge_graph(all_file_contents)
            
            # 2. Extract Subgraph starting from changed files (2-hops)
            graph_context = get_affected_subgraph(kg, changed_files, max_hops=2)
            call_chain_text = get_call_chain_context(kg, changed_files)
            
            # 3. Topological RAG: fetch external skeleton files present in the subgraph nodes
            files_in_subgraph = set()
            for node in graph_context.get("nodes", []):
                fpath = node.get("file")
                if fpath and fpath not in file_contents:
                    files_in_subgraph.add(fpath)
                    
            for imp in files_in_subgraph:
                if imp in all_file_contents:
                    skeleton = extract_skeleton(imp, all_file_contents[imp])
                    if len(skeleton.strip()) > 5:
                        external_skeletons[imp] = skeleton
                        bus.emit("file.loaded", path=f"{imp} (skeleton)", chars=len(skeleton))
        except Exception as e:
            logger.warning("Topological RAG / Knowledge graph build failed: %s", e)

    # Step 5: Get overlap diffs
    overlap_diffs: dict[str, str] = {}
    if repo and commit_sha and overlap_files:
        overlap_diffs = get_overlap_diffs(repo, commit_sha, set(overlap_files))

    # Step 6: Serialize linter findings
    linter_findings = [f.model_dump() for f in tool_results.ruff_findings + tool_results.eslint_findings]
    semgrep_findings = [f.model_dump() for f in tool_results.semgrep_findings]
    bandit_findings = [f.model_dump() for f in tool_results.bandit_findings]

    return ReviewState(
        raw_diff=raw_diff,
        changed_files=changed_files,
        overlap_files=overlap_files,
        file_contents=file_contents,
        focused_contents=focused_contents,
        diff_context=diff_context,
        external_skeletons=external_skeletons,
        call_chain_text=call_chain_text,
        graph_context=graph_context,
        linter_findings=linter_findings,
        semgrep_findings=semgrep_findings,
        bandit_findings=bandit_findings,
        overlap_diffs=overlap_diffs,
        findings=[],
        summary="",
    )
