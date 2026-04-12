"""Ruff linter runner — Python style/syntax checking."""

from __future__ import annotations

import asyncio
import json
import logging

from code_review.models import AgentName, Finding, Severity

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "E": Severity.HIGH,      # errors
    "W": Severity.MEDIUM,    # warnings
    "F": Severity.HIGH,      # pyflakes
    "C": Severity.LOW,       # conventions
    "I": Severity.LOW,       # isort
    "N": Severity.LOW,       # pep8-naming
    "S": Severity.HIGH,      # bandit (via ruff)
    "B": Severity.MEDIUM,    # bugbear
}


def _map_severity(code: str) -> Severity:
    """Map a ruff rule code prefix to severity."""
    if code:
        return SEVERITY_MAP.get(code[0], Severity.MEDIUM)
    return Severity.MEDIUM


async def run_ruff(path: str) -> list[Finding]:
    """Run ruff check on the given path, return structured findings."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ruff", "check", "--output-format", "json", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        logger.warning("ruff not found — skipping")
        return []

    if not stdout:
        return []

    try:
        results = json.loads(stdout.decode())
    except json.JSONDecodeError:
        logger.warning("Failed to parse ruff JSON output")
        return []

    findings = []
    for item in results:
        findings.append(Finding(
            severity=_map_severity(item.get("code", "")),
            file=item.get("filename", ""),
            line=item.get("location", {}).get("row", 0),
            end_line=item.get("end_location", {}).get("row"),
            message=f"[{item.get('code', '?')}] {item.get('message', '')}",
            agent=AgentName.SYNTAX,
            category="style",
        ))
    return findings
