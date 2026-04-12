"""Pre-filter node — decides which agents should run based on state."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from code_review.state import ReviewState

logger = logging.getLogger(__name__)

# File extensions that warrant security analysis
_CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".php"}


def run_prefilter(state: ReviewState) -> dict:
    """Determine which agents to run based on available data."""
    agents = []

    # Syntax: only if there are linter findings
    if state.get("linter_findings"):
        agents.append("syntax")

    # Logic: only if there's a diff or file contents to analyze
    if state.get("raw_diff") or state.get("file_contents") or state.get("focused_contents"):
        agents.append("logic")

    # Security: only if there are SAST findings OR code files (not just configs/docs)
    has_sast = state.get("semgrep_findings") or state.get("bandit_findings")
    has_code_files = any(
        PurePosixPath(f).suffix in _CODE_EXTENSIONS
        for f in state.get("changed_files", [])
    )
    if has_sast or has_code_files:
        agents.append("security")

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
