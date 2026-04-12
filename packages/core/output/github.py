"""GitHub PR review output adapter — batches findings into a single PR review."""

from __future__ import annotations

import json
import logging
from typing import Any

from code_review.models import Finding, ReviewResult, Severity
from code_review.output.base import OutputAdapter

logger = logging.getLogger(__name__)

SEVERITY_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "⚪",
}


class GitHubAdapter(OutputAdapter):
    """Builds a GitHub PR review payload (POST /repos/{owner}/{repo}/pulls/{pr}/reviews)."""

    def __init__(self, owner: str, repo: str, pr_number: int) -> None:
        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number
        self._comments: list[dict[str, Any]] = []
        self._body = ""

    def emit_progress(self, agent: str, status: str) -> None:
        pass  # No progress tracking for GitHub

    def emit_finding(self, finding: Finding) -> None:
        emoji = SEVERITY_EMOJI.get(finding.severity, "")
        body = f"{emoji} **{finding.severity.value.upper()}** ({finding.agent.value})\n\n{finding.message}"
        if finding.suggestion:
            body += f"\n\n**Suggestion:** {finding.suggestion}"

        comment: dict[str, Any] = {
            "path": finding.file,
            "body": body,
        }
        if finding.line:
            comment["line"] = finding.line
        if finding.end_line and finding.end_line != finding.line:
            comment["start_line"] = finding.line
            comment["line"] = finding.end_line

        self._comments.append(comment)

    def emit_summary(self, result: ReviewResult) -> None:
        self._body = result.summary or "Automated code review complete."

    def to_payload(self) -> dict[str, Any]:
        """Build the GitHub API payload for creating a PR review."""
        event = "COMMENT"
        if any(c for c in self._comments
               if "CRITICAL" in c.get("body", "") or "HIGH" in c.get("body", "")):
            event = "REQUEST_CHANGES"

        return {
            "body": self._body,
            "event": event,
            "comments": self._comments,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)
