"""Tests for the noise filter pipeline."""

from code_review.models import AgentName, Finding, Severity
from code_review.noise_filter import (
    apply_noise_filter,
    deduplicate,
    filter_by_severity,
    merge_overlapping,
    sort_findings,
)


def _f(severity="medium", file="a.py", line=1, end_line=None, category="style", message="test"):
    return Finding(
        severity=Severity(severity), file=file, line=line, end_line=end_line,
        message=message, agent=AgentName.SYNTAX, category=category,
    )


class TestDeduplicate:
    def test_removes_exact_dupes(self):
        findings = [_f(line=10), _f(line=10)]
        assert len(deduplicate(findings)) == 1

    def test_keeps_higher_severity(self):
        findings = [_f(severity="low", line=10), _f(severity="high", line=10)]
        result = deduplicate(findings)
        assert len(result) == 1
        assert result[0].severity == Severity.HIGH

    def test_different_lines_not_deduped(self):
        findings = [_f(line=10), _f(line=20)]
        assert len(deduplicate(findings)) == 2


class TestMergeOverlapping:
    def test_merges_overlapping(self):
        findings = [_f(line=5, end_line=15), _f(line=10, end_line=20)]
        result = merge_overlapping(findings)
        assert len(result) == 1
        assert result[0].line == 5
        assert result[0].end_line == 20

    def test_keeps_disjoint(self):
        findings = [_f(line=1, end_line=5), _f(line=10, end_line=15)]
        result = merge_overlapping(findings)
        assert len(result) == 2


class TestFilterBySeverity:
    def test_filters_below_threshold(self):
        findings = [_f(severity="low"), _f(severity="high"), _f(severity="critical")]
        result = filter_by_severity(findings, Severity.HIGH)
        assert len(result) == 2
        assert all(f.severity >= Severity.HIGH for f in result)


class TestSortFindings:
    def test_sorts_by_severity_desc(self):
        findings = [_f(severity="low"), _f(severity="critical"), _f(severity="medium")]
        result = sort_findings(findings)
        assert result[0].severity == Severity.CRITICAL
        assert result[-1].severity == Severity.LOW


class TestApplyNoiseFilter:
    def test_full_pipeline(self):
        findings = [
            _f(severity="low", line=10),
            _f(severity="high", line=10),     # dupe of above, higher severity
            _f(severity="critical", line=50),
            _f(severity="low", line=100),      # filtered by severity
        ]
        result = apply_noise_filter(findings, threshold=Severity.MEDIUM)
        assert len(result) == 2
        assert result[0].severity == Severity.CRITICAL
        assert result[1].severity == Severity.HIGH
