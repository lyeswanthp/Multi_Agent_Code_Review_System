"""Tests for Tier 1 tool runners — mock subprocess output."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from code_review.models import AgentName, Severity
from code_review.tools.bandit_runner import run_bandit
from code_review.tools.eslint_runner import run_eslint
from code_review.tools.ruff_runner import run_ruff
from code_review.tools.semgrep_runner import run_semgrep


def _mock_proc(stdout_data: str):
    """Create a mock async subprocess."""
    proc = AsyncMock()
    proc.communicate.return_value = (stdout_data.encode(), b"")
    return proc


class TestRuffRunner:
    @pytest.mark.asyncio
    async def test_parses_json_output(self):
        ruff_output = json.dumps([{
            "code": "F401",
            "message": "unused import os",
            "filename": "app.py",
            "location": {"row": 1, "column": 1},
            "end_location": {"row": 1, "column": 10},
        }])
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(ruff_output)):
            findings = await run_ruff("/tmp/test")
        assert len(findings) == 1
        assert findings[0].agent == AgentName.SYNTAX
        assert findings[0].file == "app.py"
        assert "F401" in findings[0].message

    @pytest.mark.asyncio
    async def test_returns_empty_on_not_found(self):
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    side_effect=FileNotFoundError):
            findings = await run_ruff("/tmp/test")
        assert findings == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_bad_json(self):
        with patch("code_review.tools.ruff_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc("not json")):
            findings = await run_ruff("/tmp/test")
        assert findings == []


class TestSemgrepRunner:
    @pytest.mark.asyncio
    async def test_parses_results(self):
        semgrep_output = json.dumps({"results": [{
            "check_id": "python.lang.security.audit.dangerous-subprocess",
            "path": "server.py",
            "start": {"line": 42},
            "end": {"line": 42},
            "extra": {"severity": "WARNING", "message": "Dangerous subprocess usage"},
        }]})
        with patch("code_review.tools.semgrep_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(semgrep_output)):
            findings = await run_semgrep("/tmp/test")
        assert len(findings) == 1
        assert findings[0].agent == AgentName.SECURITY
        assert findings[0].severity == Severity.HIGH

    @pytest.mark.asyncio
    async def test_returns_empty_on_not_found(self):
        with patch("code_review.tools.semgrep_runner.asyncio.create_subprocess_exec",
                    side_effect=FileNotFoundError):
            findings = await run_semgrep("/tmp/test")
        assert findings == []


class TestBanditRunner:
    @pytest.mark.asyncio
    async def test_parses_results(self):
        bandit_output = json.dumps({"results": [{
            "test_id": "B101",
            "issue_text": "Use of assert detected",
            "issue_severity": "LOW",
            "filename": "tests.py",
            "line_number": 5,
            "line_range": [5, 6],
            "more_info": "https://bandit.readthedocs.io",
        }]})
        with patch("code_review.tools.bandit_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(bandit_output)):
            findings = await run_bandit("/tmp/test")
        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW
        assert findings[0].line == 5
        assert findings[0].end_line == 6  # from line_range, not end_col_offset

    @pytest.mark.asyncio
    async def test_returns_empty_on_not_found(self):
        with patch("code_review.tools.bandit_runner.asyncio.create_subprocess_exec",
                    side_effect=FileNotFoundError):
            findings = await run_bandit("/tmp/test")
        assert findings == []


class TestEslintRunner:
    @pytest.mark.asyncio
    async def test_parses_results(self):
        eslint_output = json.dumps([{
            "filePath": "/tmp/test/app.js",
            "messages": [{
                "ruleId": "no-unused-vars",
                "severity": 1,
                "message": "'x' is defined but never used",
                "line": 3,
                "endLine": 3,
            }],
        }])
        with patch("code_review.tools.eslint_runner.asyncio.create_subprocess_exec",
                    return_value=_mock_proc(eslint_output)):
            findings = await run_eslint("/tmp/test")
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM
        assert findings[0].agent == AgentName.SYNTAX

    @pytest.mark.asyncio
    async def test_returns_empty_on_not_found(self):
        with patch("code_review.tools.eslint_runner.asyncio.create_subprocess_exec",
                    side_effect=FileNotFoundError):
            findings = await run_eslint("/tmp/test")
        assert findings == []
