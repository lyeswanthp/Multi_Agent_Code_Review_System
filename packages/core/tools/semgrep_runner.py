"""Semgrep SAST runner — security and pattern-based analysis."""

from __future__ import annotations

import asyncio
import json
import logging

from code_review.models import AgentName, Finding, Severity

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "ERROR": Severity.CRITICAL,
    "WARNING": Severity.HIGH,
    "INFO": Severity.MEDIUM,
}


async def run_semgrep(path: str) -> list[Finding]:
    """Run semgrep on the given path, return structured findings."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "semgrep", "--json", "--config", "auto", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        logger.warning("semgrep not found — skipping")
        return []

    if not stdout:
        return []

    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError:
        logger.warning("Failed to parse semgrep JSON output")
        return []

    findings = []
    for item in data.get("results", []):
        sev_str = item.get("extra", {}).get("severity", "WARNING")
        findings.append(Finding(
            severity=SEVERITY_MAP.get(sev_str, Severity.MEDIUM),
            file=item.get("path", ""),
            line=item.get("start", {}).get("line", 0),
            end_line=item.get("end", {}).get("line"),
            message=f"[{item.get('check_id', '?')}] {item.get('extra', {}).get('message', '')}",
            agent=AgentName.SECURITY,
            category="security",
        ))
    return findings
