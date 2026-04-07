"""Tests for Pydantic models."""

from code_review.models import AgentName, Finding, ReviewResult, Severity, ToolResults


class TestSeverity:
    def test_ordering(self):
        assert Severity.CRITICAL > Severity.HIGH
        assert Severity.HIGH > Severity.MEDIUM
        assert Severity.MEDIUM > Severity.LOW

    def test_rank(self):
        assert Severity.CRITICAL.rank == 4
        assert Severity.LOW.rank == 1


class TestFinding:
    def test_basic_creation(self):
        f = Finding(
            severity=Severity.HIGH,
            file="test.py",
            line=10,
            message="Bug found",
            agent=AgentName.LOGIC,
        )
        assert f.severity == Severity.HIGH
        assert f.file == "test.py"
        assert f.line == 10

    def test_overlaps_same_file_same_line(self):
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=10, message="x", agent=AgentName.LOGIC)
        f2 = Finding(severity=Severity.MEDIUM, file="a.py", line=10, message="y", agent=AgentName.SYNTAX)
        assert f1.overlaps(f2)

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


class TestToolResults:
    def test_all_findings_merges(self):
        f1 = Finding(severity=Severity.HIGH, file="a.py", line=1, message="ruff", agent=AgentName.SYNTAX)
        f2 = Finding(severity=Severity.HIGH, file="b.py", line=1, message="semgrep", agent=AgentName.SECURITY)
        tr = ToolResults(ruff_findings=[f1], semgrep_findings=[f2])
        assert len(tr.all_findings) == 2


class TestReviewResult:
    def test_exit_code_clean(self):
        r = ReviewResult()
        assert r.exit_code == 0

    def test_exit_code_findings(self):
        f = Finding(severity=Severity.LOW, file="a.py", line=1, message="x", agent=AgentName.SYNTAX)
        r = ReviewResult(findings=[f])
        assert r.exit_code == 1

    def test_has_critical(self):
        f = Finding(severity=Severity.CRITICAL, file="a.py", line=1, message="x", agent=AgentName.SECURITY)
        r = ReviewResult(findings=[f])
        assert r.has_critical
