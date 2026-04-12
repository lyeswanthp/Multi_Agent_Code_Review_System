"""Per-file agent runner — processes files one at a time for fast local inference.

Instead of batching all files into one giant prompt, this module:
1. Calls the LLM once per file with a small, focused prompt
2. Emits dashboard events after each file completes
3. Merges results at the end
"""

from __future__ import annotations

import json
import logging

from code_review.cache import get_cached, set_cached
from code_review.events import bus
from code_review.llm_client import call_agent, extract_json, truncate_content, truncate_system_prompt
from code_review.models import AgentName, Finding, Severity

logger = logging.getLogger(__name__)


async def run_per_file(
    agent_name: str,
    agent_enum: AgentName,
    system_prompt: str,
    files: dict[str, str],
    category: str = "",
    extra_context: str = "",
) -> list[Finding]:
    """Run an agent on each file individually, returning merged findings.

    Args:
        agent_name: Name for config/telemetry (e.g. "logic")
        agent_enum: AgentName enum value
        system_prompt: The system prompt (will be truncated to budget)
        files: {filepath: content} — content is already diff hunks or focused code
        category: Finding category (e.g. "logic", "security", "style")
        extra_context: Optional prefix added before each file (e.g. SAST findings)
    """
    sys_prompt = truncate_system_prompt(system_prompt)
    all_findings: list[Finding] = []

    file_list = sorted(files.items())
    total = len(file_list)

    for idx, (filepath, content) in enumerate(file_list, 1):
        if not content.strip():
            bus.emit("agent.file.skip", agent=agent_name, file=filepath, reason="empty")
            continue

        bus.emit("agent.file.start", agent=agent_name, file=filepath,
                 index=idx, total=total, chars=len(content))

        # Build a small, focused prompt for just this file
        parts = []
        if extra_context:
            parts.append(extra_context)
        parts.append(f"## {filepath}\n```\n{content}\n```")
        user_msg = truncate_content("\n".join(parts))

        # Check cache
        cached = get_cached(f"{agent_name}:{filepath}", user_msg)
        if cached is not None:
            findings = _parse_findings(cached, agent_enum, filepath, category)
            all_findings.extend(findings)
            bus.emit("agent.file.done", agent=agent_name, file=filepath,
                     findings=len(findings), cached=True)
            continue

        # Call LLM (retry once on empty response)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]
        response = await call_agent(agent_enum, messages=messages)

        if not response.strip():
            logger.info("%s agent: retrying %s after empty response", agent_name, filepath)
            response = await call_agent(agent_enum, messages=messages)

        if not response.strip():
            bus.emit("agent.file.done", agent=agent_name, file=filepath,
                     findings=0, error="empty response")
            continue

        try:
            items = extract_json(response)
        except (json.JSONDecodeError, ValueError):
            logger.warning("%s agent: non-JSON for %s — first 100 chars: %s",
                           agent_name, filepath, response[:100])
            bus.emit("agent.file.done", agent=agent_name, file=filepath,
                     findings=0, error="non-JSON response")
            continue

        if not isinstance(items, list):
            items = [items] if isinstance(items, dict) else []

        findings = _parse_findings(items, agent_enum, filepath, category)
        all_findings.extend(findings)

        set_cached(f"{agent_name}:{filepath}", user_msg, items)
        bus.emit("agent.file.done", agent=agent_name, file=filepath,
                 findings=len(findings))

    return all_findings


def _parse_findings(
    items: list[dict],
    agent_enum: AgentName,
    default_file: str,
    category: str,
) -> list[Finding]:
    findings = []
    for item in items:
        try:
            findings.append(Finding(
                severity=Severity(item.get("severity", "medium")),
                file=item.get("file", default_file) or default_file,
                line=item.get("line", 0),
                message=item.get("message", ""),
                agent=agent_enum,
                suggestion=item.get("suggestion", ""),
                category=category,
            ))
        except Exception:
            continue
    return findings
