"""Tests for output adapters — GitHubAdapter payload correctness, event logic,
edge cases for findings with/without lines, multi-line comments."""

import json

import pytest

from code_review.models import AgentName, Finding, ReviewResult, Severity
from code_review.output.github import GitHubAdapter


def _finding(severity="high", file="a.py", line=1, end_line=None, suggestion=""):
    return Finding(
        severity=Severity(severity), file=file, line=line, end_line=end_line,
        message=f"{severity} issue in {file}", agent=AgentName.LOGIC,
        suggestion=suggestion,
    )


class TestGitHubAdapterPayload:
    def test_empty_review(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        adapter.emit_summary(ReviewResult())
        payload = adapter.to_payload()
        assert payload["event"] == "COMMENT"
        assert payload["comments"] == []
        assert "complete" in payload["body"].lower()

    def test_single_finding_comment_structure(self):
        adapter = GitHubAdapter("owner", "repo", 42)
        f = _finding(severity="high", file="src/app.py", line=10, suggestion="Fix this")
        adapter.emit_finding(f)
        adapter.emit_summary(ReviewResult(findings=[f], summary="One issue"))
        payload = adapter.to_payload()
        assert len(payload["comments"]) == 1
        comment = payload["comments"][0]
        assert comment["path"] == "src/app.py"
        assert comment["line"] == 10
        assert "HIGH" in comment["body"]
        assert "Fix this" in comment["body"]

    def test_critical_finding_triggers_request_changes(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        f = _finding(severity="critical")
        adapter.emit_finding(f)
        adapter.emit_summary(ReviewResult(findings=[f]))
        payload = adapter.to_payload()
        assert payload["event"] == "REQUEST_CHANGES"

    def test_high_finding_triggers_request_changes(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        f = _finding(severity="high")
        adapter.emit_finding(f)
        adapter.emit_summary(ReviewResult(findings=[f]))
        payload = adapter.to_payload()
        assert payload["event"] == "REQUEST_CHANGES"

    def test_medium_only_stays_comment(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        f = _finding(severity="medium")
        adapter.emit_finding(f)
        adapter.emit_summary(ReviewResult(findings=[f]))
        payload = adapter.to_payload()
        assert payload["event"] == "COMMENT"

    def test_low_only_stays_comment(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        f = _finding(severity="low")
        adapter.emit_finding(f)
        adapter.emit_summary(ReviewResult(findings=[f]))
        payload = adapter.to_payload()
        assert payload["event"] == "COMMENT"

    def test_multi_line_comment(self):
        """Findings with end_line != line should produce start_line/line range."""
        adapter = GitHubAdapter("owner", "repo", 1)
        f = _finding(severity="high", line=5, end_line=15)
        adapter.emit_finding(f)
        adapter.emit_summary(ReviewResult(findings=[f]))
        payload = adapter.to_payload()
        comment = payload["comments"][0]
        assert comment["start_line"] == 5
        assert comment["line"] == 15

    def test_same_start_end_line_no_range(self):
        """When end_line == line, should NOT produce start_line."""
        adapter = GitHubAdapter("owner", "repo", 1)
        f = _finding(severity="high", line=10, end_line=10)
        adapter.emit_finding(f)
        adapter.emit_summary(ReviewResult(findings=[f]))
        comment = adapter.to_payload()["comments"][0]
        assert "start_line" not in comment
        assert comment["line"] == 10

    def test_finding_without_line(self):
        """Finding with line=0 should not set line in comment."""
        adapter = GitHubAdapter("owner", "repo", 1)
        f = Finding(
            severity=Severity.MEDIUM, file="a.py", line=0,
            message="general issue", agent=AgentName.LOGIC,
        )
        adapter.emit_finding(f)
        adapter.emit_summary(ReviewResult(findings=[f]))
        comment = adapter.to_payload()["comments"][0]
        # line=0 is falsy, so comment should not have "line" key
        assert "line" not in comment

    def test_finding_without_suggestion(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        f = _finding(severity="medium", suggestion="")
        adapter.emit_finding(f)
        adapter.emit_summary(ReviewResult(findings=[f]))
        comment = adapter.to_payload()["comments"][0]
        assert "Suggestion" not in comment["body"]

    def test_multiple_findings(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        for i in range(5):
            adapter.emit_finding(_finding(file=f"f{i}.py", line=i + 1))
        adapter.emit_summary(ReviewResult(summary="5 issues"))
        payload = adapter.to_payload()
        assert len(payload["comments"]) == 5
        assert payload["body"] == "5 issues"

    def test_to_json_is_valid_json(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        adapter.emit_finding(_finding())
        adapter.emit_summary(ReviewResult(findings=[_finding()]))
        parsed = json.loads(adapter.to_json())
        assert "event" in parsed
        assert "comments" in parsed
        assert "body" in parsed

    def test_mixed_severities_high_wins_event(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        adapter.emit_finding(_finding(severity="low"))
        adapter.emit_finding(_finding(severity="medium"))
        adapter.emit_finding(_finding(severity="high"))
        adapter.emit_summary(ReviewResult())
        payload = adapter.to_payload()
        assert payload["event"] == "REQUEST_CHANGES"

    def test_emit_progress_is_noop(self):
        """GitHubAdapter.emit_progress should not crash."""
        adapter = GitHubAdapter("owner", "repo", 1)
        adapter.emit_progress("syntax", "running")
        adapter.emit_progress("logic", "done")
        # No assertion needed — just verify no exception

    def test_summary_override(self):
        adapter = GitHubAdapter("owner", "repo", 1)
        adapter.emit_summary(ReviewResult(summary="Custom summary here"))
        assert adapter.to_payload()["body"] == "Custom summary here"
