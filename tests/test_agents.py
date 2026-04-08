"""Tests for agents — mock LLM responses to verify parsing logic."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from code_review.agents.git_history import run_git_history_agent
from code_review.agents.logic import run_logic_agent
from code_review.agents.orchestrator import run_orchestrator
from code_review.agents.security import run_security_agent
from code_review.agents.syntax import run_syntax_agent
from code_review.llm_client import extract_json
from code_review.models import AgentName, Finding, Severity


def _make_state(**overrides):
    """Create a minimal ReviewState dict with defaults."""
    base = {
        "raw_diff": "",
        "changed_files": [],
        "overlap_files": [],
        "file_contents": {},
        "focused_contents": {},
        "import_context": {},
        "linter_findings": [],
        "semgrep_findings": [],
        "bandit_findings": [],
        "overlap_diffs": {},
        "findings": [],
        "summary": "",
        "agents_to_run": [],
        "syntax_has_critical": False,
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
        assert result["syntax_has_critical"] is True  # high severity triggers this

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


class TestPrefilter:
    def test_all_agents_when_full_data(self):
        from code_review.agents.prefilter import run_prefilter
        state = _make_state(
            linter_findings=[{"code": "W001"}],
            raw_diff="some diff",
            file_contents={"app.py": "code"},
            changed_files=["app.py"],
            semgrep_findings=[{"id": "test"}],
            overlap_files=["app.py"],
        )
        result = run_prefilter(state)
        assert set(result["agents_to_run"]) == {"syntax", "logic", "security", "git_history"}

    def test_no_agents_when_empty(self):
        from code_review.agents.prefilter import run_prefilter
        state = _make_state()
        result = run_prefilter(state)
        assert result["agents_to_run"] == []

    def test_skips_security_for_non_code(self):
        from code_review.agents.prefilter import run_prefilter
        state = _make_state(
            changed_files=["README.md"],
            linter_findings=[{"code": "W001"}],
            raw_diff="diff",
        )
        result = run_prefilter(state)
        assert "security" not in result["agents_to_run"]
        assert "syntax" in result["agents_to_run"]

    def test_skips_git_history_without_overlap(self):
        from code_review.agents.prefilter import run_prefilter
        state = _make_state(
            linter_findings=[{"code": "W001"}],
            raw_diff="diff",
            changed_files=["app.py"],
        )
        result = run_prefilter(state)
        assert "git_history" not in result["agents_to_run"]


class TestCache:
    def test_cache_hit_and_miss(self):
        from code_review.cache import clear_cache, get_cached, set_cached
        clear_cache()
        assert get_cached("syntax", "content") is None
        set_cached("syntax", "content", [{"severity": "high"}])
        assert get_cached("syntax", "content") == [{"severity": "high"}]

    def test_different_content_misses(self):
        from code_review.cache import clear_cache, get_cached, set_cached
        clear_cache()
        set_cached("syntax", "content_a", [{"severity": "high"}])
        assert get_cached("syntax", "content_b") is None

    def test_clear_cache(self):
        from code_review.cache import clear_cache, get_cached, set_cached
        set_cached("syntax", "content", [{"severity": "high"}])
        clear_cache()
        assert get_cached("syntax", "content") is None


class TestExtractJson:
    def test_plain_array(self):
        result = extract_json('[{"a": 1}]')
        assert result == [{"a": 1}]

    def test_plain_object(self):
        result = extract_json('{"findings": [], "summary": "ok"}')
        assert isinstance(result, dict)
        assert result["summary"] == "ok"

    def test_fenced_array(self):
        text = "Here are the results:\n```json\n[{\"severity\": \"high\"}]\n```\nDone."
        result = extract_json(text)
        assert isinstance(result, list)
        assert result[0]["severity"] == "high"

    def test_fenced_object(self):
        text = "```json\n{\"findings\": [], \"summary\": \"clean\"}\n```"
        result = extract_json(text)
        assert isinstance(result, dict)
        assert result["summary"] == "clean"

    def test_prose_before_array(self):
        text = "I found the following issues:\n[{\"file\": \"a.py\"}]"
        result = extract_json(text)
        assert result == [{"file": "a.py"}]

    def test_prose_before_object(self):
        text = "Here is the result:\n{\"findings\": [], \"summary\": \"none\"}\nEnd."
        result = extract_json(text)
        assert isinstance(result, dict)

    def test_invalid_json_raises(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            extract_json("this is not json at all")

    def test_empty_array(self):
        assert extract_json("[]") == []

    def test_object_inside_array_picks_array(self):
        text = '[{"severity": "high", "nested": {"a": 1}}]'
        result = extract_json(text)
        assert isinstance(result, list)


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

    @pytest.mark.asyncio
    async def test_handles_fenced_json_response(self):
        """Orchestrator should parse JSON even when wrapped in markdown fences."""
        llm_response = '```json\n{"findings": [{"severity": "high", "file": "x.py", "line": 1, "message": "bug", "suggestion": "fix", "category": "logic"}], "summary": "One issue."}\n```'
        finding = Finding(
            severity=Severity.HIGH, file="x.py", line=1,
            message="raw", agent=AgentName.LOGIC,
        )
        state = _make_state(findings=[finding])

        with patch("code_review.agents.orchestrator.call_agent", new_callable=AsyncMock, return_value=llm_response):
            result = await run_orchestrator(state)

        assert result["summary"] == "One issue."
        assert len(result["findings"]) == 1

    @pytest.mark.asyncio
    async def test_handles_prose_wrapped_json(self):
        """Orchestrator should parse JSON even with prose around it."""
        llm_response = 'Here is my analysis:\n{"findings": [], "summary": "All clean."}\nHope this helps!'
        finding = Finding(
            severity=Severity.LOW, file="a.py", line=1,
            message="raw", agent=AgentName.SYNTAX,
        )
        state = _make_state(findings=[finding])

        with patch("code_review.agents.orchestrator.call_agent", new_callable=AsyncMock, return_value=llm_response):
            result = await run_orchestrator(state)

        assert result["summary"] == "All clean."
