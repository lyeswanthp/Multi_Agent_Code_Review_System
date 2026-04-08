"""LangGraph shared state definition."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from code_review.models import Finding


class ReviewState(TypedDict):
    """Shared state passed through the LangGraph review pipeline.

    - Tier 1 populates: raw_diff, changed_files, overlap_files, linter_findings
    - Tier 2 populates: file_contents, import_context
    - Tier 3 agents READ state and APPEND to findings via the reducer
    - Orchestrator reads merged findings, writes summary
    """

    # Diff and file data (written once by Tier 2, read by agents)
    raw_diff: str
    changed_files: list[str]
    overlap_files: list[str]
    file_contents: dict[str, str]
    focused_contents: dict[str, str]  # AST-extracted relevant blocks only
    import_context: dict[str, list[str]]

    # Linter output (written by Tier 1, read by Syntax/Security agents)
    linter_findings: list[dict]

    # SAST output separated for Security agent
    semgrep_findings: list[dict]
    bandit_findings: list[dict]

    # Git history diffs for overlap files (written by Tier 1)
    overlap_diffs: dict[str, str]

    # Agent findings — reducer merges parallel results via list concatenation
    findings: Annotated[list[Finding], operator.add]

    # Final summary (written by orchestrator)
    summary: str
