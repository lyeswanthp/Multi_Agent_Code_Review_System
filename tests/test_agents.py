"""Tests for agents — mock LLM responses to verify parsing logic."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from code_review.agents.git_history import run_git_history_agent
from code_review.agents.logic import run_logic_agent
from code_review.agents.orchestrator import run_orchestrator
from code_review.agents.security import run_security_agent
from code_review.agents.syntax import run_syntax_agent
from code_review.models import AgentName, Finding, Severity


def _make_state(**overrides):
    """Create a minimal ReviewState dict with defaults."""
    base = {
        "raw_diff": "",
        "changed_files": [],
        "overlap_files": [],
        "file_contents": {},
        "import_context": {},
        "linter_findings": [],
        "semgrep_findings": [],
        "bandit_findings": [],
        "overlap_diffs": {},
        "findings": [],
        "summary": "",
    }
    base.update(overrides)
    return base


class TestSyntaxAgent:
    @pytest.mark.asyncio
    async def test_parses_llm_response(self):
        llm_response = json.dumps([{
            "severity": "high",
            "file": "app.py",
            "line": 5,
            "message": "Unused import",
            "suggestion": "Remove import os",
        }])
        state = _make_state(linter_findings=[{"code": "F401", "file": "app.py"}])

        with patch("code_review.agents.syntax.call_agent", new_callable=AsyncMock, return_value=llm_response):
            result = await run_syntax_agent(state)

        assert len(result["findings"]) == 1
        assert result["findings"][0].agent == AgentName.SYNTAX

    @pytest.mark.asyncio
    async def test_skips_when_no_findings(self):
        state = _make_state()
        result = await run_syntax_agent(state)
        assert result["findings"] == []

    @pytest.mark.asyncio
    async def test_handles_empty_llm_response(self):
        state = _make_state(linter_findings=[{"code": "F401"}])
        with patch("code_review.agents.syntax.call_agent", new_callable=AsyncMock, return_value=""):
            result = await run_syntax_agent(state)
        assert result["findings"] == []

    @pytest.mark.asyncio
    async def test_handles_bad_json(self):
        state = _make_state(linter_findings=[{"code": "F401"}])
        with patch("code_review.agents.syntax.call_agent", new_callable=AsyncMock, return_value="not json"):
            result = await run_syntax_agent(state)
        assert result["findings"] == []


class TestLogicAgent:
    @pytest.mark.asyncio
    async def test_parses_llm_response(self):
        llm_response = json.dumps([{
            "severity": "critical",
            "file": "auth.py",
            "line": 42,
            "message": "Off-by-one in range check",
            "suggestion": "Use <= instead of <",
        }])
        state = _make_state(
            raw_diff="--- a/auth.py\n+++ b/auth.py",
            file_contents={"auth.py": "def check(x): return x < 10"},
        )

        with patch("code_review.agents.logic.call_agent", new_callable=AsyncMock, return_value=llm_response):
            result = await run_logic_agent(state)

        assert len(result["findings"]) == 1
        assert result["findings"][0].severity == Severity.CRITICAL
        assert result["findings"][0].agent == AgentName.LOGIC

    @pytest.mark.asyncio
    async def test_skips_when_empty(self):
        state = _make_state()
        result = await run_logic_agent(state)
        assert result["findings"] == []


class TestSecurityAgent:
    @pytest.mark.asyncio
    async def test_parses_llm_response(self):
        llm_response = json.dumps([{
            "severity": "critical",
            "file": "server.py",
            "line": 15,
            "message": "SQL injection via user input",
            "suggestion": "Use parameterized queries",
        }])
        state = _make_state(
            semgrep_findings=[{"check_id": "sql-injection"}],
            file_contents={"server.py": "query = f'SELECT * FROM users WHERE id={user_id}'"},
        )

        with patch("code_review.agents.security.call_agent", new_callable=AsyncMock, return_value=llm_response):
            result = await run_security_agent(state)

        assert len(result["findings"]) == 1
        assert result["findings"][0].agent == AgentName.SECURITY

    @pytest.mark.asyncio
    async def test_skips_when_empty(self):
        state = _make_state()
        result = await run_security_agent(state)
        assert result["findings"] == []


class TestGitHistoryAgent:
    @pytest.mark.asyncio
    async def test_skips_when_no_overlap(self):
        state = _make_state()
        result = await run_git_history_agent(state)
        assert result["findings"] == []

    @pytest.mark.asyncio
    async def test_parses_llm_response(self):
        llm_response = json.dumps([{
            "severity": "medium",
            "file": "utils.py",
            "line": 0,
            "message": "Same function patched in consecutive commits",
            "suggestion": "Investigate root cause",
        }])
        state = _make_state(
            overlap_files=["utils.py"],
            overlap_diffs={"utils.py": "--- a/utils.py\n+++ b/utils.py"},
        )

        with patch("code_review.agents.git_history.call_agent", new_callable=AsyncMock, return_value=llm_response):
            result = await run_git_history_agent(state)

        assert len(result["findings"]) == 1
        assert result["findings"][0].agent == AgentName.GIT_HISTORY


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_returns_clean_when_no_findings(self):
        state = _make_state()
        result = await run_orchestrator(state)
        assert result["summary"] == "No issues found. Code looks clean."

    @pytest.mark.asyncio
    async def test_parses_synthesized_response(self):
        llm_response = json.dumps({
            "findings": [{
                "severity": "high",
                "file": "app.py",
                "line": 10,
                "message": "Unified finding",
                "suggestion": "Fix it",
                "category": "logic",
            }],
            "summary": "One high severity issue found.",
        })
        finding = Finding(
            severity=Severity.HIGH, file="app.py", line=10,
            message="raw", agent=AgentName.LOGIC,
        )
        state = _make_state(findings=[finding])

        with patch("code_review.agents.orchestrator.call_agent", new_callable=AsyncMock, return_value=llm_response):
            result = await run_orchestrator(state)

        assert result["summary"] == "One high severity issue found."
        assert len(result["findings"]) == 1

    @pytest.mark.asyncio
    async def test_handles_llm_failure(self):
        finding = Finding(
            severity=Severity.HIGH, file="app.py", line=10,
            message="raw", agent=AgentName.LOGIC,
        )
        state = _make_state(findings=[finding])

        with patch("code_review.agents.orchestrator.call_agent", new_callable=AsyncMock, return_value=""):
            result = await run_orchestrator(state)

        assert "unavailable" in result["summary"].lower()
