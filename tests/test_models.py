"""Aggressive model tests — validation edge cases, boundary overlaps,
serialization round-trips, invalid inputs, Pydantic constraint enforcement."""

import pytest
from pydantic import ValidationError

from code_review.models import AgentName, Finding, ReviewResult, Severity, ToolResults


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_full_ordering_chain(self):
        assert Severity.CRITICAL > Severity.HIGH > Severity.MEDIUM > Severity.LOW

    def test_reverse_ordering(self):
        assert Severity.LOW < Severity.MEDIUM < Severity.HIGH < Severity.CRITICAL

    def test_equality_not_gt(self):
        assert not (Severity.HIGH > Severity.HIGH)
        assert Severity.HIGH >= Severity.HIGH
        assert Severity.HIGH <= Severity.HIGH

    def test_rank_values(self):
        assert Severity.LOW.rank == 1
        assert Severity.MEDIUM.rank == 2
        assert Severity.HIGH.rank == 3
        assert Severity.CRITICAL.rank == 4

    def test_from_string(self):
        assert Severity("critical") == Severity.CRITICAL
        assert Severity("low") == Severity.LOW

    def test_invalid_severity_raises(self):
        with pytest.raises(ValueError):
            Severity("urgent")

    def test_case_sensitive(self):
        with pytest.raises(ValueError):
            Severity("HIGH")

    def test_str_value(self):
        assert Severity.CRITICAL.value == "critical"
        assert str(Severity.CRITICAL) == "Severity.CRITICAL" or "critical" in str(Severity.CRITICAL)

    @pytest.mark.parametrize("a,b,expected", [
        (Severity.CRITICAL, Severity.LOW, True),
        (Severity.LOW, Severity.CRITICAL, False),
        (Severity.MEDIUM, Severity.MEDIUM, False),
    ])
    def test_gt_parametrized(self, a, b, expected):
        assert (a > b) is expected


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

class TestFinding:
    def test_minimal_creation(self):
        f = Finding(severity=Severity.LOW, file="a.py", message="x", agent=AgentName.SYNTAX)
        assert f.line == 0
        assert f.end_line is None
        assert f.suggestion == ""
        assert f.category == ""

    def test_full_creation(self):
        f = Finding(
            severity=Severity.CRITICAL, file="a.py", line=10, end_line=20,
            message="bug", agent=AgentName.LOGIC, suggestion="fix", category="logic",
        )
        assert f.end_line == 20
        assert f.suggestion == "fix"

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            Finding(severity=Severity.HIGH, file="a.py")  # missing message and agent

    def test_invalid_severity_type_raises(self):
        with pytest.raises(ValidationError):
            Finding(severity="not_valid", file="a.py", message="x", agent=AgentName.SYNTAX)

    def test_invalid_agent_type_raises(self):
        with pytest.raises(ValidationError):
            Finding(severity=Severity.HIGH, file="a.py", message="x", agent="not_valid")

    # --- overlaps ---

    def test_overlaps_same_line_point(self):
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=10, message="x", agent=AgentName.LOGIC)
        f2 = Finding(severity=Severity.LOW, file="a.py", line=10, message="y", agent=AgentName.SYNTAX)
        assert f1.overlaps(f2)
        assert f2.overlaps(f1)  # symmetric

    def test_no_overlap_different_files(self):
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=10, message="x", agent=AgentName.LOGIC)
        f2 = Finding(severity=Severity.HIGH, file="b.py", line=10, message="y", agent=AgentName.LOGIC)
        assert not f1.overlaps(f2)

    def test_overlaps_range(self):
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=5, end_line=15, message="x", agent=AgentName.LOGIC)
        f2 = Finding(severity=Severity.HIGH, file="a.py", line=10, end_line=20, message="y", agent=AgentName.LOGIC)
        assert f1.overlaps(f2)

    def test_no_overlap_disjoint_ranges(self):
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=5, end_line=9, message="x", agent=AgentName.LOGIC)
        f2 = Finding(severity=Severity.HIGH, file="a.py", line=10, end_line=20, message="y", agent=AgentName.LOGIC)
        assert not f1.overlaps(f2)

    def test_overlap_touching_boundary(self):
        """end_line=10 and line=10 should overlap (they share line 10)."""
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=5, end_line=10, message="x", agent=AgentName.LOGIC)
        f2 = Finding(severity=Severity.HIGH, file="a.py", line=10, end_line=15, message="y", agent=AgentName.LOGIC)
        assert f1.overlaps(f2)

    def test_overlap_point_inside_range(self):
        """Point finding at line 12 overlaps with range 5-15."""
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=5, end_line=15, message="x", agent=AgentName.LOGIC)
        f2 = Finding(severity=Severity.HIGH, file="a.py", line=12, message="y", agent=AgentName.LOGIC)
        assert f1.overlaps(f2)

    def test_overlap_point_outside_range(self):
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=5, end_line=10, message="x", agent=AgentName.LOGIC)
        f2 = Finding(severity=Severity.HIGH, file="a.py", line=20, message="y", agent=AgentName.LOGIC)
        assert not f1.overlaps(f2)

    def test_overlap_zero_line(self):
        """line=0 (default) should still work with overlap logic."""
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=0, message="x", agent=AgentName.LOGIC)
        f2 = Finding(severity=Severity.HIGH, file="a.py", line=0, message="y", agent=AgentName.LOGIC)
        assert f1.overlaps(f2)

    def test_serialization_round_trip(self):
        f = Finding(
            severity=Severity.CRITICAL, file="test.py", line=42, end_line=50,
            message="critical bug", agent=AgentName.SECURITY,
            suggestion="fix it", category="security",
        )
        dumped = f.model_dump()
        restored = Finding(**dumped)
        assert restored == f

    def test_json_round_trip(self):
        f = Finding(
            severity=Severity.HIGH, file="x.py", line=1,
            message="test", agent=AgentName.LOGIC,
        )
        json_str = f.model_dump_json()
        restored = Finding.model_validate_json(json_str)
        assert restored == f


# ---------------------------------------------------------------------------
# ToolResults
# ---------------------------------------------------------------------------

class TestToolResults:
    def test_all_findings_merges_all_four(self):
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=1, message="ruff", agent=AgentName.SYNTAX)
        f2 = Finding(severity=Severity.HIGH, file="b.py", line=1, message="semgrep", agent=AgentName.SECURITY)
        f3 = Finding(severity=Severity.LOW, file="c.py", line=1, message="bandit", agent=AgentName.SECURITY)
        f4 = Finding(severity=Severity.MEDIUM, file="d.js", line=1, message="eslint", agent=AgentName.SYNTAX)
        tr = ToolResults(ruff_findings=[f1], semgrep_findings=[f2], bandit_findings=[f3], eslint_findings=[f4])
        assert len(tr.all_findings) == 4

    def test_empty_tool_results(self):
        tr = ToolResults()
        assert tr.all_findings == []
        assert tr.changed_files == set()
        assert tr.raw_diff == ""

    def test_changed_files_is_set(self):
        tr = ToolResults(changed_files={"a.py", "b.py", "a.py"})
        assert len(tr.changed_files) == 2


# ---------------------------------------------------------------------------
# ReviewResult
# ---------------------------------------------------------------------------

class TestReviewResult:
    def test_clean_exit_code(self):
        r = ReviewResult()
        assert r.exit_code == 0
        assert not r.has_critical

    def test_findings_exit_code(self):
        f = Finding(severity=Severity.LOW, file="a.py", line=1, message="x", agent=AgentName.SYNTAX)
        r = ReviewResult(findings=[f])
        assert r.exit_code == 1

    def test_has_critical_true(self):
        f = Finding(severity=Severity.CRITICAL, file="a.py", line=1, message="x", agent=AgentName.SECURITY)
        r = ReviewResult(findings=[f])
        assert r.has_critical

    def test_has_critical_false_with_high(self):
        f = Finding(severity=Severity.HIGH, file="a.py", line=1, message="x", agent=AgentName.SECURITY)
        r = ReviewResult(findings=[f])
        assert not r.has_critical

    def test_metadata_preserved(self):
        r = ReviewResult(metadata={"files_reviewed": 10, "custom_key": "value"})
        assert r.metadata["files_reviewed"] == 10
        assert r.metadata["custom_key"] == "value"

    def test_serialization_round_trip(self):
        f = Finding(severity=Severity.HIGH, file="a.py", line=1, message="x", agent=AgentName.LOGIC)
        r = ReviewResult(findings=[f], summary="One issue", metadata={"k": "v"})
        dumped = r.model_dump()
        restored = ReviewResult(**dumped)
        assert len(restored.findings) == 1
        assert restored.summary == "One issue"


# ---------------------------------------------------------------------------
# AgentName
# ---------------------------------------------------------------------------

class TestAgentName:
    def test_all_values(self):
        expected = {"syntax", "logic", "security", "git_history", "orchestrator"}
        actual = {a.value for a in AgentName}
        assert actual == expected

    def test_from_string(self):
        assert AgentName("syntax") == AgentName.SYNTAX

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            AgentName("unknown_agent")
