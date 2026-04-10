"""Syntax Agent — interprets linter output per file."""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from code_review.agents.per_file import run_per_file
from code_review.events import agent_telemetry, bus
from code_review.models import AgentName, Severity
from code_review.rules.loader import load_rules
from code_review.state import ReviewState

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = """\
Analyze these linter findings for one file. Return ONLY JSON:
[{"severity":"high|medium|low","file":"path","line":0,"message":"issue","suggestion":"fix"}]
"""


def _get_system_prompt() -> str:
    rules = load_rules()
    rule = rules.get("syntax")
    return rule.body if (rule and rule.body) else FALLBACK_PROMPT


@agent_telemetry("syntax")
async def run_syntax_agent(state: ReviewState) -> dict:
    """Analyze linter findings per file."""
    linter_findings = state["linter_findings"]

    if not linter_findings:
        return {"findings": []}

    # Group linter findings by file
    by_file: dict[str, list[dict]] = defaultdict(list)
    for f in linter_findings:
        filepath = f.get("file", f.get("filename", "unknown"))
        by_file[filepath].append(f)

    bus.emit("agent.files", agent="syntax",
             files=sorted(by_file.keys()), count=len(linter_findings))

    # Build per-file content: just the linter findings for that file
    file_contents = {}
    for filepath, findings in by_file.items():
        file_contents[filepath] = json.dumps(findings, indent=2, default=str)

    all_findings = await run_per_file(
        agent_name="syntax",
        agent_enum=AgentName.SYNTAX,
        system_prompt=_get_system_prompt(),
        files=file_contents,
        category="style",
    )

    has_critical = any(f.severity == Severity.CRITICAL for f in all_findings)
    return {"findings": all_findings, "syntax_has_critical": has_critical}
