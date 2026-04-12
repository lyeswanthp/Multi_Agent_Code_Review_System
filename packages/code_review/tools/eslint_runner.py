"""ESLint runner — JavaScript/TypeScript linting."""

from __future__ import annotations

import asyncio
import json
import logging

from code_review.models import AgentName, Finding, Severity

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    2: Severity.HIGH,     # error
    1: Severity.MEDIUM,   # warning
}


async def run_eslint(path: str) -> list[Finding]:
    """Run eslint on the given path, return structured findings."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "eslint", "--format", "json", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        logger.warning("eslint not found — skipping")
        return []

    if not stdout:
        return []

    try:
        results = json.loads(stdout.decode())
    except json.JSONDecodeError:
        logger.warning("Failed to parse eslint JSON output")
        return []

    findings = []
    for file_result in results:
        filepath = file_result.get("filePath", "")
        for msg in file_result.get("messages", []):
            findings.append(Finding(
                severity=SEVERITY_MAP.get(msg.get("severity", 1), Severity.MEDIUM),
                file=filepath,
                line=msg.get("line", 0),
                end_line=msg.get("endLine"),
                message=f"[{msg.get('ruleId', '?')}] {msg.get('message', '')}",
                agent=AgentName.SYNTAX,
                category="style",
            ))
    return findings
