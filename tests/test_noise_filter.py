"""Aggressive noise filter tests — cross-file non-merge, category boundaries,
empty inputs, single finding, same-severity dedup, merge chains, full pipeline stress."""

import pytest

from code_review.models import AgentName, Finding, Severity
from code_review.noise_filter import (
    apply_noise_filter,
    deduplicate,
    filter_by_severity,
    merge_overlapping,
    sort_findings,
)


def _f(severity="medium", file="a.py", line=1, end_line=None, category="style",
       message="test", agent=AgentName.SYNTAX):
    return Finding(
        severity=Severity(severity), file=file, line=line, end_line=end_line,
        message=message, agent=agent, category=category,
    )


# ---------------------------------------------------------------------------
# Deduplicate
# ---------------------------------------------------------------------------

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

    def test_same_line_different_categories_not_deduped(self):
        findings = [_f(line=10, category="style"), _f(line=10, category="logic")]
        assert len(deduplicate(findings)) == 2

    def test_same_line_different_files_not_deduped(self):
        findings = [_f(file="a.py", line=10), _f(file="b.py", line=10)]
        assert len(deduplicate(findings)) == 2

    def test_same_severity_keeps_one(self):
        findings = [_f(severity="medium", line=5), _f(severity="medium", line=5)]
        result = deduplicate(findings)
        assert len(result) == 1

    def test_empty_input(self):
        assert deduplicate([]) == []

    def test_single_finding(self):
        result = deduplicate([_f()])
        assert len(result) == 1

    def test_three_dupes_different_severities(self):
        findings = [
            _f(severity="low", line=10),
            _f(severity="critical", line=10),
            _f(severity="medium", line=10),
        ]
        result = deduplicate(findings)
        assert len(result) == 1
        assert result[0].severity == Severity.CRITICAL

    def test_end_line_matters_for_dedup_key(self):
        """Findings at same line but different end_line are different."""
        findings = [_f(line=5, end_line=10), _f(line=5, end_line=15)]
        result = deduplicate(findings)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Merge overlapping
# ---------------------------------------------------------------------------

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

    def test_cross_file_never_merges(self):
        """Findings in different files should never merge even if lines overlap."""
        findings = [
            _f(file="a.py", line=5, end_line=15),
            _f(file="b.py", line=10, end_line=20),
        ]
        result = merge_overlapping(findings)
        assert len(result) == 2

    def test_cross_category_never_merges(self):
        """Same file, overlapping lines, but different categories — no merge."""
        findings = [
            _f(line=5, end_line=15, category="style"),
            _f(line=10, end_line=20, category="security"),
        ]
        result = merge_overlapping(findings)
        assert len(result) == 2

    def test_chain_merge_three(self):
        """Three overlapping findings should merge into one."""
        findings = [
            _f(line=1, end_line=10),
            _f(line=8, end_line=20),
            _f(line=18, end_line=30),
        ]
        result = merge_overlapping(findings)
        assert len(result) == 1
        assert result[0].line == 1
        assert result[0].end_line == 30

    def test_merged_keeps_higher_severity(self):
        findings = [
            _f(severity="low", line=5, end_line=15),
            _f(severity="critical", line=10, end_line=20),
        ]
        result = merge_overlapping(findings)
        assert len(result) == 1
        assert result[0].severity == Severity.CRITICAL

    def test_merged_combines_messages(self):
        findings = [
            _f(line=5, end_line=15, message="issue A"),
            _f(line=10, end_line=20, message="issue B"),
        ]
        result = merge_overlapping(findings)
        assert "issue A" in result[0].message
        assert "issue B" in result[0].message

    def test_empty_input(self):
        assert merge_overlapping([]) == []

    def test_single_finding(self):
        result = merge_overlapping([_f()])
        assert len(result) == 1

    def test_adjacent_but_not_overlapping(self):
        """end_line=9 and line=10 should NOT overlap (line 10 is the start of next range)."""
        findings = [_f(line=5, end_line=9), _f(line=10, end_line=15)]
        result = merge_overlapping(findings)
        assert len(result) == 2

    def test_point_findings_same_line_merge(self):
        """Two findings at same line with no end_line should merge."""
        findings = [_f(line=10, end_line=None), _f(line=10, end_line=None)]
        result = merge_overlapping(findings)
        assert len(result) == 1

    def test_merged_suggestion_falls_back(self):
        """When first finding has no suggestion, merged should use second's."""
        findings = [
            _f(line=5, end_line=15, message="a"),
            Finding(severity=Severity.HIGH, file="a.py", line=10, end_line=20,
                    message="b", agent=AgentName.SYNTAX, suggestion="fix it", category="style"),
        ]
        result = merge_overlapping(findings)
        assert result[0].suggestion == "fix it"


# ---------------------------------------------------------------------------
# Filter by severity
# ---------------------------------------------------------------------------

class TestFilterBySeverity:
    def test_filters_below_threshold(self):
        findings = [_f(severity="low"), _f(severity="high"), _f(severity="critical")]
        result = filter_by_severity(findings, Severity.HIGH)
        assert len(result) == 2

    def test_threshold_low_keeps_all(self):
        findings = [_f(severity="low"), _f(severity="medium"), _f(severity="high")]
        result = filter_by_severity(findings, Severity.LOW)
        assert len(result) == 3

    def test_threshold_critical_only_critical(self):
        findings = [_f(severity="low"), _f(severity="high"), _f(severity="critical")]
        result = filter_by_severity(findings, Severity.CRITICAL)
        assert len(result) == 1
        assert result[0].severity == Severity.CRITICAL

    def test_empty_input(self):
        assert filter_by_severity([], Severity.LOW) == []

    def test_all_filtered_out(self):
        findings = [_f(severity="low"), _f(severity="low")]
        result = filter_by_severity(findings, Severity.HIGH)
        assert result == []


# ---------------------------------------------------------------------------
# Sort findings
# ---------------------------------------------------------------------------

class TestSortFindings:
    def test_sorts_severity_desc(self):
        findings = [_f(severity="low"), _f(severity="critical"), _f(severity="medium")]
        result = sort_findings(findings)
        assert result[0].severity == Severity.CRITICAL
        assert result[-1].severity == Severity.LOW

    def test_same_severity_sorts_by_file_then_line(self):
        findings = [
            _f(severity="high", file="b.py", line=20),
            _f(severity="high", file="a.py", line=10),
            _f(severity="high", file="a.py", line=5),
        ]
        result = sort_findings(findings)
        assert result[0].file == "a.py" and result[0].line == 5
        assert result[1].file == "a.py" and result[1].line == 10
        assert result[2].file == "b.py"

    def test_empty_input(self):
        assert sort_findings([]) == []


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

class TestApplyNoiseFilter:
    def test_full_pipeline_dedup_merge_filter_sort(self):
        findings = [
            _f(severity="low", line=10),
            _f(severity="high", line=10),       # dupe, higher severity
            _f(severity="critical", line=50),
            _f(severity="low", line=100),        # filtered by threshold
        ]
        result = apply_noise_filter(findings, threshold=Severity.MEDIUM)
        assert len(result) == 2
        assert result[0].severity == Severity.CRITICAL
        assert result[1].severity == Severity.HIGH

    def test_pipeline_with_overlapping_ranges(self):
        findings = [
            _f(severity="medium", line=1, end_line=10),
            _f(severity="high", line=8, end_line=20),
            _f(severity="critical", line=50, end_line=60),
        ]
        result = apply_noise_filter(findings, threshold=Severity.MEDIUM)
        assert len(result) == 2  # first two merged
        assert result[0].severity == Severity.CRITICAL  # sorted first
        assert result[1].line == 1 and result[1].end_line == 20  # merged range

    def test_pipeline_empty(self):
        assert apply_noise_filter([]) == []

    def test_pipeline_all_same_severity_deduped(self):
        """10 identical findings should collapse to 1."""
        findings = [_f(severity="medium", line=5) for _ in range(10)]
        result = apply_noise_filter(findings, threshold=Severity.LOW)
        assert len(result) == 1

    def test_pipeline_many_files_no_cross_merge(self):
        """Findings across 5 files should never merge."""
        findings = [_f(file=f"f{i}.py", line=10, end_line=20) for i in range(5)]
        result = apply_noise_filter(findings, threshold=Severity.LOW)
        assert len(result) == 5
