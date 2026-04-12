"""Git History Agent — detects regressions and repeated changes in overlapping files.

Provider: Groq | Model: llama-3.1-8b-instant
"""

from __future__ import annotations

import json
import logging

from code_review.cache import get_cached, set_cached
from code_review.events import agent_telemetry
from code_review.llm_client import call_agent, extract_json
from code_review.models import AgentName, Finding, Severity
from code_review.rules.loader import load_rules
from code_review.state import ReviewState

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = """\
You are a git history agent. Analyze overlapping files between commits for regressions and patterns.
Return ONLY a JSON array: [{"severity":"high|medium|low","file":"...","line":0,"message":"...","suggestion":"..."}]
If no issues, return: []
"""


def _get_system_prompt() -> str:
    rules = load_rules()
    rule = rules.get("git_history")
    if rule and rule.body:
        return rule.body
    return FALLBACK_PROMPT


@agent_telemetry("git_history")
async def run_git_history_agent(state: ReviewState) -> dict:
    """Analyze overlapping files between current and previous commit."""
    overlap_files = state["overlap_files"]
    overlap_diffs = state["overlap_diffs"]

    if not overlap_files:
        logger.info("Git history agent: no overlapping files, skipping")
        return {"findings": []}

    context_parts = [f"Files changed in both current and previous commit: {', '.join(overlap_files)}\n"]

    for filepath, diff in overlap_diffs.items():
        context_parts.append(f"## {filepath}\n```diff\n{diff}\n```\n")

    user_msg = "\n".join(context_parts)

    # Check cache
    cached = get_cached("git_history", user_msg)
    if cached is not None:
        findings = []
        for item in cached:
            findings.append(Finding(
                severity=Severity(item.get("severity", "medium")),
                file=item.get("file", ""),
                line=item.get("line", 0),
                message=item.get("message", ""),
                agent=AgentName.GIT_HISTORY,
                suggestion=item.get("suggestion", ""),
                category="git_history",
            ))
        return {"findings": findings}

    response = await call_agent(
        AgentName.GIT_HISTORY,
        messages=[
            {"role": "system", "content": _get_system_prompt()},
            {"role": "user", "content": user_msg},
        ],
    )

    if not response:
        return {"findings": []}

    try:
        items = extract_json(response)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Git history agent returned non-JSON response")
        return {"findings": []}

    if not isinstance(items, list):
        items = [items] if isinstance(items, dict) else []

    findings = []
    for item in items:
        findings.append(Finding(
            severity=Severity(item.get("severity", "medium")),
            file=item.get("file", ""),
            line=item.get("line", 0),
            message=item.get("message", ""),
            agent=AgentName.GIT_HISTORY,
            suggestion=item.get("suggestion", ""),
            category="git_history",
        ))

    set_cached("git_history", user_msg, items)
    return {"findings": findings}
