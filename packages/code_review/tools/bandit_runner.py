"""Bandit security scanner runner — Python-specific security checks."""

from __future__ import annotations

import asyncio
import json
import logging

from code_review.models import AgentName, Finding, Severity

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "HIGH": Severity.HIGH,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
}

CONFIDENCE_BOOST = {"HIGH": 1, "MEDIUM": 0, "LOW": -1}


async def run_bandit(path: str) -> list[Finding]:
    """Run bandit on the given path, return structured findings."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "bandit", "-r", path, "-f", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        logger.warning("bandit not found — skipping")
        return []

    if not stdout:
        return []

    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError:
        logger.warning("Failed to parse bandit JSON output")
        return []

    findings = []
    for item in data.get("results", []):
        findings.append(Finding(
            severity=SEVERITY_MAP.get(item.get("issue_severity", ""), Severity.MEDIUM),
            file=item.get("filename", ""),
            line=item.get("line_number", 0),
            end_line=item.get("line_range", [None, None])[-1],
            message=f"[{item.get('test_id', '?')}] {item.get('issue_text', '')}",
            agent=AgentName.SECURITY,
            category="security",
            suggestion=item.get("more_info", ""),
        ))
    return findings
