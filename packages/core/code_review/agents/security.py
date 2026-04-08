"""Security Agent — triages SAST findings, separates real threats from false positives.

Provider: Cerebras | Model: qwen3-32b
"""

from __future__ import annotations

import json
import logging

from code_review.llm_client import call_agent, extract_json, truncate_content
from code_review.models import AgentName, Finding, Severity
from code_review.rules.loader import load_rules
from code_review.state import ReviewState

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = """\
You are a security review agent. Triage SAST findings and hunt for missed vulnerabilities.
Return ONLY a JSON array: [{"severity":"critical|high|medium|low","file":"...","line":0,"message":"...","suggestion":"..."}]
"""


def _get_system_prompt() -> str:
    rules = load_rules()
    rule = rules.get("security")
    if rule and rule.body:
        return rule.body
    return FALLBACK_PROMPT


async def run_security_agent(state: ReviewState) -> dict:
    """Triage SAST findings and hunt for missed security issues."""
    semgrep = state["semgrep_findings"]
    bandit = state["bandit_findings"]
    focused_contents = state["focused_contents"]
    file_contents = state["file_contents"]

    # Prefer focused (AST-extracted) content; fall back to full files
    contents = focused_contents if focused_contents else file_contents

    if not semgrep and not bandit and not contents:
        logger.info("Security agent: no findings or files, skipping")
        return {"findings": []}

    n_files = len(contents)
    per_file_budget = max(2000, 16_000 // (n_files + 1)) if n_files else 16_000

    context_parts = []

    if semgrep or bandit:
        sast_text = f"## SAST Findings\n### Semgrep\n{json.dumps(semgrep, indent=2)}\n### Bandit\n{json.dumps(bandit, indent=2)}\n"
        context_parts.append(truncate_content(sast_text, 4000))

    for filepath, content in contents.items():
        truncated = truncate_content(content, per_file_budget)
        context_parts.append(f"## {filepath}\n```\n{truncated}\n```\n")

    user_msg = truncate_content("\n".join(context_parts))

    response = await call_agent(
        AgentName.SECURITY,
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
        logger.warning("Security agent returned non-JSON response")
        return {"findings": []}

    findings = []
    for item in items:
        findings.append(Finding(
            severity=Severity(item.get("severity", "medium")),
            file=item.get("file", ""),
            line=item.get("line", 0),
            message=item.get("message", ""),
            agent=AgentName.SECURITY,
            suggestion=item.get("suggestion", ""),
            category="security",
        ))

    return {"findings": findings}
