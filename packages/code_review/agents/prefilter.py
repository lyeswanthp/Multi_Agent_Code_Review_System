"""Pre-filter node — decides which agents should run based on state."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from code_review.state import ReviewState

logger = logging.getLogger(__name__)

# File extensions that warrant security analysis
_CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".php"}


def run_prefilter(state: ReviewState) -> dict:
    """Determine which agents to run based on available data.

    Master agent handles syntax/logic/security in one pass.
    Git history runs separately (different input).
    """
    agents = []

    # Master: runs when there's diff content or code files to analyze
    # (combines syntax/logic/security in a single LLM call)
    has_code = (
        state.get("raw_diff") or
        state.get("file_contents") or
        state.get("focused_contents") or
        state.get("linter_findings") or
        state.get("semgrep_findings") or
        state.get("bandit_findings")
    )
    has_code_files = any(
        PurePosixPath(f).suffix in _CODE_EXTENSIONS
        for f in state.get("changed_files", [])
    )
    if has_code or has_code_files:
        agents.append("master")

    # Git history: only if there are overlapping files
    if state.get("overlap_files"):
        agents.append("git_history")

    from code_review.events import bus

    if not agents:
        logger.info("Pre-filter: no agents needed, skipping to orchestrator")
    else:
        logger.info("Pre-filter: agents to run: %s", agents)

    bus.emit("agent.set", agents=agents)
    return {"agents_to_run": agents, "syntax_has_critical": False}
