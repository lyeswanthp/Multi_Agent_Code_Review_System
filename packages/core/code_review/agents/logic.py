"""Logic Agent — deep code reasoning with semi-formal analysis.

Provider: NVIDIA NIM | Model: mistralai/devstral-2-123b-instruct-2512
"""

from __future__ import annotations

import json
import logging

from code_review.cache import get_cached, set_cached
from code_review.llm_client import call_agent, extract_json, truncate_content
from code_review.models import AgentName, Finding, Severity
from code_review.rules.loader import load_rules
from code_review.state import ReviewState

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = """\
You are a logic review agent. Analyze code for bugs, edge cases, and logic errors.
Return ONLY a JSON array: [{"severity":"critical|high|medium|low","file":"...","line":0,"message":"...","suggestion":"..."}]
"""


def _get_system_prompt() -> str:
    rules = load_rules()
    rule = rules.get("logic")
    if rule and rule.body:
        return rule.body
    return FALLBACK_PROMPT


async def run_logic_agent(state: ReviewState) -> dict:
    """Analyze code diff and files for logic errors and edge cases."""
    raw_diff = state["raw_diff"]
    focused_contents = state["focused_contents"]
    file_contents = state["file_contents"]
    import_context = state["import_context"]

    # Prefer focused (AST-extracted) content; fall back to full files
    contents = focused_contents if focused_contents else file_contents

    if not raw_diff and not contents:
        logger.info("Logic agent: no diff or files, skipping")
        return {"findings": []}

    # Build context: diff + file contents (each file truncated to fit context window)
    n_files = len(contents)
    per_file_budget = max(2000, 16_000 // (n_files + 1)) if n_files else 16_000
    diff_budget = min(4000, per_file_budget)

    context_parts = [f"## Diff\n```\n{raw_diff[:diff_budget]}\n```\n"]

    for filepath, content in contents.items():
        imports = import_context.get(filepath, [])
        import_note = f" (imports: {', '.join(imports)})" if imports else ""
        truncated = truncate_content(content, per_file_budget)
        context_parts.append(f"## {filepath}{import_note}\n```\n{truncated}\n```\n")

    user_msg = truncate_content("\n".join(context_parts))

    # Check cache
    cached = get_cached("logic", user_msg)
    if cached is not None:
        findings = []
        for item in cached:
            findings.append(Finding(
                severity=Severity(item.get("severity", "medium")),
                file=item.get("file", ""),
                line=item.get("line", 0),
                message=item.get("message", ""),
                agent=AgentName.LOGIC,
                suggestion=item.get("suggestion", ""),
                category="logic",
            ))
        return {"findings": findings}

    response = await call_agent(
        AgentName.LOGIC,
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
        logger.warning("Logic agent returned non-JSON response")
        return {"findings": []}

    findings = []
    for item in items:
        findings.append(Finding(
            severity=Severity(item.get("severity", "medium")),
            file=item.get("file", ""),
            line=item.get("line", 0),
            message=item.get("message", ""),
            agent=AgentName.LOGIC,
            suggestion=item.get("suggestion", ""),
            category="logic",
        ))

    set_cached("logic", user_msg, items)
    return {"findings": findings}
