"""Syntax Agent — interprets linter output into human-readable findings.

Provider: Groq | Model: llama-3.3-70b-versatile
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
You are a syntax and style review agent. Analyze linter output and produce prioritized findings.
Return ONLY a JSON array: [{"severity":"high|medium|low","file":"...","line":0,"message":"...","suggestion":"..."}]
"""


def _get_system_prompt() -> str:
    rules = load_rules()
    rule = rules.get("syntax")
    if rule and rule.body:
        return rule.body
    return FALLBACK_PROMPT


async def run_syntax_agent(state: ReviewState) -> dict:
    """Analyze linter findings and return human-readable interpretations."""
    linter_findings = state["linter_findings"]

    if not linter_findings:
        logger.info("Syntax agent: no linter findings, skipping")
        return {"findings": []}

    user_msg = truncate_content(f"Linter findings to analyze:\n{json.dumps(linter_findings, indent=2)}")

    # Check cache
    cached = get_cached("syntax", user_msg)
    if cached is not None:
        items = cached
        findings = []
        for item in items:
            findings.append(Finding(
                severity=Severity(item.get("severity", "medium")),
                file=item.get("file", ""),
                line=item.get("line", 0),
                message=item.get("message", ""),
                agent=AgentName.SYNTAX,
                suggestion=item.get("suggestion", ""),
                category="style",
            ))
        has_critical = any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings)
        return {"findings": findings, "syntax_has_critical": has_critical}

    response = await call_agent(
        AgentName.SYNTAX,
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
        logger.warning("Syntax agent returned non-JSON response")
        return {"findings": []}

    findings = []
    for item in items:
        findings.append(Finding(
            severity=Severity(item.get("severity", "medium")),
            file=item.get("file", ""),
            line=item.get("line", 0),
            message=item.get("message", ""),
            agent=AgentName.SYNTAX,
            suggestion=item.get("suggestion", ""),
            category="style",
        ))

    set_cached("syntax", user_msg, items)
    has_critical = any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings)
    return {"findings": findings, "syntax_has_critical": has_critical}
