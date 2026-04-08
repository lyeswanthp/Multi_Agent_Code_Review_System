"""Aggressive tool runner tests — stderr handling, empty stdout, multiple files,
severity mapping coverage, truncated JSON, non-zero exit codes, missing fields."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from code_review.models import AgentName, Severity
from code_review.tools.bandit_runner import run_bandit
from code_review.tools.eslint_runner import run_eslint
from code_review.tools.ruff_runner import run_ruff, _map_severity
from code_review.tools.semgrep_runner import run_semgrep


def _mock_proc(stdout_data: str, stderr_data: str = ""):
    proc = AsyncMock()
    proc.communicate.return_value = (stdout_data.encode(), stderr_data.encode())
    proc.returncode = 0
    return proc


# ---------------------------------------------------------------------------
# Ruff
# ---------------------------------------------------------------------------

class TestRuffRunner:
    @pytest.mark.asyncio
    async def test_multiple_findings_multiple_files(self):
        output = json.dumps([
            {"code": "F401", "message": "unused import", "filename": "a.py",
             "location": {"row": 1, "column": 1}, "end_location": {"row": 1, "column": 10}},
            {"code": "E501", "message": "line too long", "filename": "a.py",
             "location": {"row": 50, "column": 1}, "end_location": {"row": 50, "column": 120}},
            {"code": "W291", "message": "trailing whitespace", "filename": "b.py",
             "location": {"row": 3, "column": 1}, "end_location": {"row": 3, "column": 5}},
        ])
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_ruff("/tmp")
        assert len(findings) == 3
        assert {f.file for f in findings} == {"a.py", "b.py"}

    @pytest.mark.asyncio
    async def test_empty_stdout_returns_empty(self):
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc("")):
            findings = await run_ruff("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_stderr_present_still_parses_stdout(self):
        output = json.dumps([{"code": "F401", "message": "unused", "filename": "a.py",
                              "location": {"row": 1, "column": 1}, "end_location": {"row": 1, "column": 5}}])
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output, "WARNING: something")):
            findings = await run_ruff("/tmp")
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_truncated_json(self):
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc('[{"code": "F401", "message": "un')):
            findings = await run_ruff("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_empty_json_array(self):
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc("[]")):
            findings = await run_ruff("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_missing_location_fields(self):
        """Tool output with missing nested location — should not crash."""
        output = json.dumps([{"code": "F401", "message": "unused", "filename": "a.py",
                              "location": {}, "end_location": {}}])
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_ruff("/tmp")
        assert len(findings) == 1
        assert findings[0].line == 0  # default when row missing

    @pytest.mark.asyncio
    async def test_missing_code_field(self):
        output = json.dumps([{"message": "unknown", "filename": "a.py",
                              "location": {"row": 1, "column": 1}, "end_location": {"row": 1, "column": 5}}])
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_ruff("/tmp")
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM  # default for empty code

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    side_effect=FileNotFoundError):
            findings = await run_ruff("/tmp")
        assert findings == []


class TestRuffSeverityMapping:
    """Every prefix in SEVERITY_MAP exercised, plus unknown prefix."""

    @pytest.mark.parametrize("code,expected", [
        ("E501", Severity.HIGH),
        ("W291", Severity.MEDIUM),
        ("F401", Severity.HIGH),
        ("C901", Severity.LOW),
        ("I001", Severity.LOW),
        ("N802", Severity.LOW),
        ("S101", Severity.HIGH),
        ("B006", Severity.MEDIUM),
        ("Z999", Severity.MEDIUM),  # unknown prefix
        ("", Severity.MEDIUM),      # empty code
    ])
    def test_severity_map(self, code, expected):
        assert _map_severity(code) == expected


# ---------------------------------------------------------------------------
# Semgrep
# ---------------------------------------------------------------------------

class TestSemgrepRunner:
    @pytest.mark.asyncio
    async def test_multiple_results(self):
        output = json.dumps({"results": [
            {"check_id": "sqli", "path": "a.py", "start": {"line": 10}, "end": {"line": 12},
             "extra": {"severity": "ERROR", "message": "SQL injection"}},
            {"check_id": "xss", "path": "b.py", "start": {"line": 5}, "end": {"line": 5},
             "extra": {"severity": "WARNING", "message": "XSS possible"}},
        ]})
        with patch("code_review.tools.semgrep_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_semgrep("/tmp")
        assert len(findings) == 2
        assert findings[0].severity == Severity.CRITICAL
        assert findings[1].severity == Severity.HIGH

    @pytest.mark.asyncio
    async def test_empty_results_array(self):
        output = json.dumps({"results": []})
        with patch("code_review.tools.semgrep_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_semgrep("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_missing_extra_field(self):
        output = json.dumps({"results": [
            {"check_id": "test", "path": "a.py", "start": {"line": 1}, "end": {"line": 1}},
        ]})
        with patch("code_review.tools.semgrep_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_semgrep("/tmp")
        assert len(findings) == 1
        # Missing extra → .get("severity", "WARNING") → maps to HIGH
        assert findings[0].severity == Severity.HIGH

    @pytest.mark.asyncio
    async def test_unknown_severity_string(self):
        output = json.dumps({"results": [
            {"check_id": "test", "path": "a.py", "start": {"line": 1}, "end": {"line": 1},
             "extra": {"severity": "UNKNOWN_LEVEL", "message": "test"}},
        ]})
        with patch("code_review.tools.semgrep_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_semgrep("/tmp")
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM  # default for unknown

    @pytest.mark.asyncio
    async def test_bad_json(self):
        with patch("code_review.tools.semgrep_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc("ERROR: config invalid")):
            findings = await run_semgrep("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_no_results_key(self):
        """Semgrep outputs JSON but without 'results' key."""
        output = json.dumps({"errors": [{"message": "config not found"}]})
        with patch("code_review.tools.semgrep_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_semgrep("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with patch("code_review.tools.semgrep_runner.asyncio.create_subprocess_exec",
                    side_effect=FileNotFoundError):
            findings = await run_semgrep("/tmp")
        assert findings == []


# ---------------------------------------------------------------------------
# Bandit
# ---------------------------------------------------------------------------

class TestBanditRunner:
    @pytest.mark.asyncio
    async def test_multiple_findings(self):
        output = json.dumps({"results": [
            {"test_id": "B101", "issue_text": "assert", "issue_severity": "LOW",
             "filename": "t.py", "line_number": 5, "line_range": [5, 6], "more_info": "url1"},
            {"test_id": "B105", "issue_text": "hardcoded password", "issue_severity": "HIGH",
             "filename": "s.py", "line_number": 10, "line_range": [10], "more_info": "url2"},
        ]})
        with patch("code_review.tools.bandit_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_bandit("/tmp")
        assert len(findings) == 2
        assert findings[0].severity == Severity.LOW
        assert findings[0].end_line == 6
        assert findings[1].severity == Severity.HIGH
        assert findings[1].end_line == 10  # single-element line_range

    @pytest.mark.asyncio
    async def test_missing_line_range(self):
        output = json.dumps({"results": [
            {"test_id": "B101", "issue_text": "assert", "issue_severity": "LOW",
             "filename": "t.py", "line_number": 5, "more_info": ""},
        ]})
        with patch("code_review.tools.bandit_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_bandit("/tmp")
        assert len(findings) == 1
        # line_range missing — end_line should be None
        assert findings[0].end_line is None

    @pytest.mark.asyncio
    async def test_unknown_severity(self):
        output = json.dumps({"results": [
            {"test_id": "B999", "issue_text": "new check", "issue_severity": "CRITICAL",
             "filename": "t.py", "line_number": 1, "line_range": [1], "more_info": ""},
        ]})
        with patch("code_review.tools.bandit_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_bandit("/tmp")
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM  # CRITICAL not in SEVERITY_MAP

    @pytest.mark.asyncio
    async def test_empty_results(self):
        output = json.dumps({"results": []})
        with patch("code_review.tools.bandit_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_bandit("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_bad_json(self):
        with patch("code_review.tools.bandit_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc("Run started...")):
            findings = await run_bandit("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with patch("code_review.tools.bandit_runner.asyncio.create_subprocess_exec",
                    side_effect=FileNotFoundError):
            findings = await run_bandit("/tmp")
        assert findings == []


# ---------------------------------------------------------------------------
# ESLint
# ---------------------------------------------------------------------------

class TestEslintRunner:
    @pytest.mark.asyncio
    async def test_multiple_files_multiple_messages(self):
        output = json.dumps([
            {"filePath": "/tmp/a.js", "messages": [
                {"ruleId": "no-unused-vars", "severity": 1, "message": "unused x", "line": 3, "endLine": 3},
                {"ruleId": "no-console", "severity": 2, "message": "no console", "line": 10, "endLine": 10},
            ]},
            {"filePath": "/tmp/b.js", "messages": [
                {"ruleId": "semi", "severity": 1, "message": "missing semi", "line": 1, "endLine": 1},
            ]},
        ])
        with patch("code_review.tools.eslint_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_eslint("/tmp")
        assert len(findings) == 3
        assert findings[1].severity == Severity.HIGH  # severity 2 = error

    @pytest.mark.asyncio
    async def test_file_with_no_messages(self):
        output = json.dumps([{"filePath": "/tmp/clean.js", "messages": []}])
        with patch("code_review.tools.eslint_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_eslint("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_unknown_severity_number(self):
        output = json.dumps([{"filePath": "/tmp/a.js", "messages": [
            {"ruleId": "custom", "severity": 99, "message": "custom rule", "line": 1},
        ]}])
        with patch("code_review.tools.eslint_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_eslint("/tmp")
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM  # default

    @pytest.mark.asyncio
    async def test_missing_rule_id(self):
        output = json.dumps([{"filePath": "/tmp/a.js", "messages": [
            {"severity": 2, "message": "syntax error", "line": 1},
        ]}])
        with patch("code_review.tools.eslint_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(output)):
            findings = await run_eslint("/tmp")
        assert len(findings) == 1
        assert "?" in findings[0].message or "None" in findings[0].message

    @pytest.mark.asyncio
    async def test_empty_json_array(self):
        with patch("code_review.tools.eslint_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc("[]")):
            findings = await run_eslint("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_bad_json(self):
        with patch("code_review.tools.eslint_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc("Oops! Something went wrong!")):
            findings = await run_eslint("/tmp")
        assert findings == []

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with patch("code_review.tools.eslint_runner.asyncio.create_subprocess_exec",
                    side_effect=FileNotFoundError):
            findings = await run_eslint("/tmp")
        assert findings == []
