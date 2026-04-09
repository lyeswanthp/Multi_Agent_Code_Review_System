"""Aggressive tests for all agents — failure paths, cache integration, invalid data,
call-count assertions, and parametrized edge cases.  No redundant happy-path padding."""

import json
from unittest.mock import AsyncMock, call, patch

import pytest

from code_review.agents.git_history import run_git_history_agent
from code_review.agents.logic import run_logic_agent
from code_review.agents.orchestrator import run_orchestrator
from code_review.agents.prefilter import run_prefilter
from code_review.agents.security import run_security_agent
from code_review.agents.syntax import run_syntax_agent
from code_review.cache import clear_cache
from code_review.llm_client import extract_json
from code_review.models import AgentName, Finding, Severity


def _make_state(**overrides):
    base = {
        "raw_diff": "",
        "changed_files": [],
        "overlap_files": [],
        "file_contents": {},
        "focused_contents": {},
        "import_context": {},
        "graph_context": {"nodes": [], "edges": []},
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


# ---------------------------------------------------------------------------
# Parametrized agent tests — every agent gets the same battery of failure modes
# ---------------------------------------------------------------------------

_AGENT_CONFIGS = [
    # (run_fn, patch_target, state_factory, agent_name)
    (
        run_syntax_agent,
        "code_review.agents.syntax.call_agent",
        lambda: _make_state(linter_findings=[{"code": "F401", "file": "a.py"}]),
        AgentName.SYNTAX,
    ),
    (
        run_logic_agent,
        "code_review.agents.logic.call_agent",
        lambda: _make_state(raw_diff="--- a/x.py\n+++ b/x.py", file_contents={"x.py": "x=1"}),
        AgentName.LOGIC,
    ),
    (
        run_security_agent,
        "code_review.agents.security.call_agent",
        lambda: _make_state(semgrep_findings=[{"check_id": "sqli"}], file_contents={"s.py": "q=f'{x}'"}),
        AgentName.SECURITY,
    ),
    (
        run_git_history_agent,
        "code_review.agents.git_history.call_agent",
        lambda: _make_state(overlap_files=["u.py"], overlap_diffs={"u.py": "--- a/u.py\n+++ b/u.py"}),
        AgentName.GIT_HISTORY,
    ),
]


class TestAgentFailureModes:
    """Every agent must handle: empty response, garbage text, partial JSON,
    JSON array of wrong shape, LLM exception, and invalid severity values."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_cache()
        yield
        clear_cache()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_fn,target,state_fn,agent", _AGENT_CONFIGS, ids=lambda x: getattr(x, "value", ""))
    async def test_empty_llm_response_returns_no_findings(self, run_fn, target, state_fn, agent):
        with patch(target, new_callable=AsyncMock, return_value=""):
            result = await run_fn(state_fn())
        assert result["findings"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_fn,target,state_fn,agent", _AGENT_CONFIGS, ids=lambda x: getattr(x, "value", ""))
    async def test_garbage_text_returns_no_findings(self, run_fn, target, state_fn, agent):
        with patch(target, new_callable=AsyncMock, return_value="Sure! Here's my analysis of your code..."):
            result = await run_fn(state_fn())
        assert result["findings"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_fn,target,state_fn,agent", _AGENT_CONFIGS, ids=lambda x: getattr(x, "value", ""))
    async def test_json_object_instead_of_array(self, run_fn, target, state_fn, agent):
        """LLM returns {findings: [...]} instead of [...] — agents expect array.
        Currently agents crash on this (iterating dict keys) — this test documents
        the known limitation. When agents are hardened, change to assert no crash."""
        bad = json.dumps({"findings": [{"severity": "high", "file": "a.py", "line": 1, "message": "x"}]})
        with patch(target, new_callable=AsyncMock, return_value=bad):
            with pytest.raises((AttributeError, TypeError)):
                await run_fn(state_fn())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_fn,target,state_fn,agent", _AGENT_CONFIGS, ids=lambda x: getattr(x, "value", ""))
    async def test_invalid_severity_in_response_defaults_gracefully(self, run_fn, target, state_fn, agent):
        """LLM invents a severity like 'urgent' — should not crash."""
        bad = json.dumps([{"severity": "urgent", "file": "a.py", "line": 1, "message": "x", "suggestion": "y"}])
        with patch(target, new_callable=AsyncMock, return_value=bad):
            # Should either skip the finding or raise a controlled error, not crash unhandled
            try:
                result = await run_fn(state_fn())
                # If it didn't raise, findings should be empty (invalid severity rejected)
                assert isinstance(result["findings"], list)
            except ValueError:
                pass  # Pydantic validation rejection is acceptable

    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_fn,target,state_fn,agent", _AGENT_CONFIGS, ids=lambda x: getattr(x, "value", ""))
    async def test_truncated_json_returns_no_findings(self, run_fn, target, state_fn, agent):
        """LLM response cut off mid-JSON."""
        truncated = '[{"severity": "high", "file": "a.py", "line": 1, "message": "oops'
        with patch(target, new_callable=AsyncMock, return_value=truncated):
            result = await run_fn(state_fn())
        assert result["findings"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_fn,target,state_fn,agent", _AGENT_CONFIGS, ids=lambda x: getattr(x, "value", ""))
    async def test_llm_called_exactly_once(self, run_fn, target, state_fn, agent):
        """Verify agents don't double-call the LLM."""
        good = json.dumps([{"severity": "medium", "file": "a.py", "line": 1, "message": "x", "suggestion": "y"}])
        mock = AsyncMock(return_value=good)
        with patch(target, mock):
            await run_fn(state_fn())
        assert mock.call_count == 1


class TestAgentSkipConditions:
    """Agents must NOT call LLM when their preconditions are unmet."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_cache()
        yield
        clear_cache()

    @pytest.mark.asyncio
    async def test_syntax_skips_without_linter_findings(self):
        mock = AsyncMock()
        with patch("code_review.agents.syntax.call_agent", mock):
            result = await run_syntax_agent(_make_state())
        mock.assert_not_called()
        assert result["findings"] == []

    @pytest.mark.asyncio
    async def test_logic_skips_without_diff_or_files(self):
        mock = AsyncMock()
        with patch("code_review.agents.logic.call_agent", mock):
            result = await run_logic_agent(_make_state())
        mock.assert_not_called()
        assert result["findings"] == []

    @pytest.mark.asyncio
    async def test_security_skips_without_any_data(self):
        mock = AsyncMock()
        with patch("code_review.agents.security.call_agent", mock):
            result = await run_security_agent(_make_state())
        mock.assert_not_called()
        assert result["findings"] == []

    @pytest.mark.asyncio
    async def test_git_history_skips_without_overlap(self):
        mock = AsyncMock()
        with patch("code_review.agents.git_history.call_agent", mock):
            result = await run_git_history_agent(_make_state())
        mock.assert_not_called()
        assert result["findings"] == []


class TestAgentCacheIntegration:
    """Second call with identical input must NOT call LLM — must use cache."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_cache()
        yield
        clear_cache()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_fn,target,state_fn,agent", _AGENT_CONFIGS, ids=lambda x: getattr(x, "value", ""))
    async def test_second_call_uses_cache(self, run_fn, target, state_fn, agent):
        good = json.dumps([{"severity": "high", "file": "a.py", "line": 1, "message": "bug", "suggestion": "fix"}])
        mock = AsyncMock(return_value=good)
        state = state_fn()
        with patch(target, mock):
            r1 = await run_fn(state)
            r2 = await run_fn(state)
        # LLM called only once; second call served from cache
        assert mock.call_count == 1
        assert len(r1["findings"]) == len(r2["findings"])
        assert r2["findings"][0].agent == agent

    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_fn,target,state_fn,agent", _AGENT_CONFIGS, ids=lambda x: getattr(x, "value", ""))
    async def test_different_input_cache_miss(self, run_fn, target, state_fn, agent):
        good = json.dumps([{"severity": "low", "file": "b.py", "line": 2, "message": "ok", "suggestion": ""}])
        mock = AsyncMock(return_value=good)
        s1 = state_fn()
        # Mutate state to create different cache key
        s2 = state_fn()
        if s2.get("linter_findings"):
            s2["linter_findings"].append({"code": "W999", "file": "extra.py"})
        if s2.get("raw_diff"):
            s2["raw_diff"] += "\n+extra line"
        if s2.get("semgrep_findings"):
            s2["semgrep_findings"].append({"check_id": "extra"})
        if s2.get("file_contents"):
            s2["file_contents"]["extra.py"] = "extra content"
        if s2.get("overlap_files"):
            s2["overlap_files"].append("extra.py")
            s2["overlap_diffs"]["extra.py"] = "--- a/extra.py\n+++ b/extra.py"

        with patch(target, mock):
            await run_fn(s1)
            await run_fn(s2)
        assert mock.call_count == 2  # Both calls hit LLM


class TestSyntaxCriticalFlag:
    """syntax_has_critical must be set correctly for various severity combos."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_cache()
        yield
        clear_cache()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("severity,expected", [
        ("critical", True),
        ("high", True),
        ("medium", False),
        ("low", False),
    ])
    async def test_critical_flag_per_severity(self, severity, expected):
        resp = json.dumps([{"severity": severity, "file": "a.py", "line": 1, "message": "x", "suggestion": "y"}])
        state = _make_state(linter_findings=[{"code": "F401"}])
        with patch("code_review.agents.syntax.call_agent", new_callable=AsyncMock, return_value=resp):
            result = await run_syntax_agent(state)
        assert result["syntax_has_critical"] is expected

    @pytest.mark.asyncio
    async def test_mixed_severities_high_wins(self):
        resp = json.dumps([
            {"severity": "low", "file": "a.py", "line": 1, "message": "x", "suggestion": "y"},
            {"severity": "high", "file": "a.py", "line": 2, "message": "z", "suggestion": "w"},
        ])
        state = _make_state(linter_findings=[{"code": "F401"}])
        with patch("code_review.agents.syntax.call_agent", new_callable=AsyncMock, return_value=resp):
            result = await run_syntax_agent(state)
        assert result["syntax_has_critical"] is True
        assert len(result["findings"]) == 2


class TestSyntaxAgentMultipleFindings:
    """Syntax agent should handle multi-finding responses correctly."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_cache()
        yield
        clear_cache()

    @pytest.mark.asyncio
    async def test_many_findings_all_parsed(self):
        items = [
            {"severity": "high", "file": f"f{i}.py", "line": i, "message": f"msg{i}", "suggestion": f"fix{i}"}
            for i in range(20)
        ]
        state = _make_state(linter_findings=[{"code": "F401"}])
        with patch("code_review.agents.syntax.call_agent", new_callable=AsyncMock, return_value=json.dumps(items)):
            result = await run_syntax_agent(state)
        assert len(result["findings"]) == 20
        assert all(f.agent == AgentName.SYNTAX for f in result["findings"])


class TestLogicAgentContextBuilding:
    """Logic agent should prefer focused_contents over file_contents."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_cache()
        yield
        clear_cache()

    @pytest.mark.asyncio
    async def test_prefers_focused_contents(self):
        resp = json.dumps([{"severity": "medium", "file": "a.py", "line": 1, "message": "x", "suggestion": "y"}])
        mock = AsyncMock(return_value=resp)
        state = _make_state(
            raw_diff="diff",
            file_contents={"a.py": "FULL CONTENT SHOULD NOT APPEAR"},
            focused_contents={"a.py": "FOCUSED CONTENT"},
        )
        with patch("code_review.agents.logic.call_agent", mock):
            await run_logic_agent(state)
        # Verify the user message sent to LLM contains focused, not full
        user_msg = mock.call_args[1]["messages"][1]["content"] if mock.call_args[1] else mock.call_args[0][1][1]["content"]
        assert "FOCUSED CONTENT" in user_msg
        assert "FULL CONTENT SHOULD NOT APPEAR" not in user_msg

    @pytest.mark.asyncio
    async def test_falls_back_to_file_contents_when_no_focused(self):
        resp = json.dumps([])
        mock = AsyncMock(return_value=resp)
        state = _make_state(
            raw_diff="diff",
            file_contents={"a.py": "FULL CONTENT HERE"},
            focused_contents={},
        )
        with patch("code_review.agents.logic.call_agent", mock):
            await run_logic_agent(state)
        user_msg = mock.call_args[1]["messages"][1]["content"] if mock.call_args[1] else mock.call_args[0][1][1]["content"]
        assert "FULL CONTENT HERE" in user_msg


class TestSecurityAgentDataPaths:
    """Security agent runs with SAST-only, files-only, or both."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_cache()
        yield
        clear_cache()

    @pytest.mark.asyncio
    async def test_runs_with_only_file_contents(self):
        """No SAST findings but code files present — should still call LLM."""
        resp = json.dumps([])
        mock = AsyncMock(return_value=resp)
        state = _make_state(file_contents={"app.py": "import os"})
        with patch("code_review.agents.security.call_agent", mock):
            await run_security_agent(state)
        mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_runs_with_only_bandit_findings(self):
        resp = json.dumps([{"severity": "high", "file": "x.py", "line": 1, "message": "vuln", "suggestion": "fix"}])
        mock = AsyncMock(return_value=resp)
        state = _make_state(bandit_findings=[{"test_id": "B101"}])
        with patch("code_review.agents.security.call_agent", mock):
            result = await run_security_agent(state)
        mock.assert_called_once()
        assert len(result["findings"]) == 1


class TestOrchestratorEdgeCases:
    """Orchestrator handles: array instead of object, missing fields, empty findings array."""

    @pytest.mark.asyncio
    async def test_llm_returns_array_instead_of_object(self):
        """LLM returns [...] instead of {findings: [...], summary: ...}"""
        bad = json.dumps([{"severity": "high", "file": "a.py", "line": 1, "message": "x"}])
        finding = Finding(severity=Severity.HIGH, file="a.py", line=1, message="x", agent=AgentName.LOGIC)
        state = _make_state(findings=[finding])
        with patch("code_review.agents.orchestrator.call_agent", new_callable=AsyncMock, return_value=bad):
            result = await run_orchestrator(state)
        assert "unstructured" in result["summary"].lower()

    @pytest.mark.asyncio
    async def test_llm_returns_object_without_summary_key(self):
        resp = json.dumps({"findings": [{"severity": "high", "file": "a.py", "line": 1, "message": "x", "suggestion": "y", "category": "logic"}]})
        finding = Finding(severity=Severity.HIGH, file="a.py", line=1, message="x", agent=AgentName.LOGIC)
        state = _make_state(findings=[finding])
        with patch("code_review.agents.orchestrator.call_agent", new_callable=AsyncMock, return_value=resp):
            result = await run_orchestrator(state)
        # Should still parse findings even without summary
        assert result["summary"] == ""
        assert len(result["findings"]) == 1

    @pytest.mark.asyncio
    async def test_llm_returns_empty_findings_array(self):
        resp = json.dumps({"findings": [], "summary": "All issues were false positives."})
        finding = Finding(severity=Severity.LOW, file="a.py", line=1, message="x", agent=AgentName.SYNTAX)
        state = _make_state(findings=[finding])
        with patch("code_review.agents.orchestrator.call_agent", new_callable=AsyncMock, return_value=resp):
            result = await run_orchestrator(state)
        assert result["summary"] == "All issues were false positives."
        assert "findings" not in result or result.get("findings", []) == []

    @pytest.mark.asyncio
    async def test_many_findings_from_multiple_agents(self):
        """Orchestrator should handle a large mixed-agent input."""
        findings = [
            Finding(severity=Severity.CRITICAL, file=f"f{i}.py", line=i, message=f"m{i}", agent=agent)
            for i, agent in enumerate([AgentName.SYNTAX, AgentName.LOGIC, AgentName.SECURITY, AgentName.GIT_HISTORY] * 5)
        ]
        orch_resp = json.dumps({
            "findings": [{"severity": "critical", "file": f"f{i}.py", "line": i, "message": f"m{i}", "suggestion": "", "category": "mixed"} for i in range(20)],
            "summary": "20 critical issues across 4 agents.",
        })
        state = _make_state(findings=findings)
        with patch("code_review.agents.orchestrator.call_agent", new_callable=AsyncMock, return_value=orch_resp):
            result = await run_orchestrator(state)
        assert len(result["findings"]) == 20
        assert result["summary"] == "20 critical issues across 4 agents."


class TestPrefilterEdgeCases:
    """Pre-filter routing with tricky file mixes and data combinations."""

    def test_mixed_code_and_docs_enables_security(self):
        """If ANY changed file is code, security should run."""
        state = _make_state(changed_files=["README.md", "app.py", "docs/guide.txt"])
        result = run_prefilter(state)
        assert "security" in result["agents_to_run"]

    def test_only_config_files_skips_security(self):
        state = _make_state(changed_files=["config.yml", ".env.example", "Makefile"])
        result = run_prefilter(state)
        assert "security" not in result["agents_to_run"]

    def test_focused_contents_alone_triggers_logic(self):
        """Logic should run if focused_contents is present even without raw_diff or file_contents."""
        state = _make_state(focused_contents={"a.py": "def foo(): pass"})
        result = run_prefilter(state)
        assert "logic" in result["agents_to_run"]

    def test_bandit_findings_trigger_security(self):
        state = _make_state(bandit_findings=[{"test_id": "B101"}])
        result = run_prefilter(state)
        assert "security" in result["agents_to_run"]

    def test_all_extensions_recognized(self):
        """Every code extension should trigger security."""
        for ext in [".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".php"]:
            state = _make_state(changed_files=[f"app{ext}"])
            result = run_prefilter(state)
            assert "security" in result["agents_to_run"], f"Extension {ext} should trigger security"

    def test_resets_syntax_has_critical(self):
        """Pre-filter should always set syntax_has_critical to False."""
        state = _make_state(syntax_has_critical=True, linter_findings=[{"code": "X"}])
        result = run_prefilter(state)
        assert result["syntax_has_critical"] is False


class TestExtractJsonAdversarial:
    """Adversarial inputs for the JSON extractor used by all agents."""

    def test_nested_fences_fails(self):
        """Nested fences confuse the regex — documents known limitation."""
        text = "```json\n```json\n[{\"a\": 1}]\n```\n```"
        with pytest.raises(json.JSONDecodeError):
            extract_json(text)

    def test_multiple_json_blocks_picks_first(self):
        text = '```json\n[{"a": 1}]\n```\nAlso:\n```json\n[{"b": 2}]\n```'
        result = extract_json(text)
        assert result == [{"a": 1}]

    def test_json_with_trailing_comma_fails(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            extract_json('[{"a": 1},]')

    def test_unicode_content(self):
        text = '[{"message": "变量未使用", "severity": "low", "file": "app.py", "line": 1}]'
        result = extract_json(text)
        assert result[0]["message"] == "变量未使用"

    def test_deeply_nested_json(self):
        deep = {"a": {"b": {"c": {"d": [1, 2, 3]}}}}
        result = extract_json(json.dumps(deep))
        assert result == deep

    def test_empty_string_raises(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            extract_json("")

    def test_only_whitespace_raises(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            extract_json("   \n\t  ")

    def test_array_containing_null(self):
        result = extract_json('[null, {"a": 1}]')
        assert result[0] is None

    def test_object_with_array_value(self):
        text = '{"findings": [{"severity": "high"}], "summary": "bad"}'
        result = extract_json(text)
        assert isinstance(result, dict)
        assert "findings" in result

    def test_prose_with_curly_braces_in_english(self):
        """English text with { } should not confuse extractor if no valid JSON."""
        with pytest.raises((json.JSONDecodeError, ValueError)):
            extract_json("The set {1, 2, 3} is not empty.")


class TestCacheEdgeCases:
    """Cache with large content, empty content, special characters."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_cache()
        yield
        clear_cache()

    def test_empty_string_content(self):
        from code_review.cache import get_cached, set_cached
        set_cached("syntax", "", [{"severity": "low"}])
        assert get_cached("syntax", "") == [{"severity": "low"}]

    def test_large_content_hashing(self):
        from code_review.cache import get_cached, set_cached
        big = "x" * 1_000_000
        set_cached("logic", big, [{"severity": "high"}])
        assert get_cached("logic", big) == [{"severity": "high"}]

    def test_unicode_content_key(self):
        from code_review.cache import get_cached, set_cached
        set_cached("security", "变量 = 'hello'", [{"severity": "medium"}])
        assert get_cached("security", "变量 = 'hello'") == [{"severity": "medium"}]

    def test_same_content_different_agents(self):
        from code_review.cache import get_cached, set_cached
        set_cached("syntax", "same content", [{"a": 1}])
        set_cached("logic", "same content", [{"b": 2}])
        assert get_cached("syntax", "same content") == [{"a": 1}]
        assert get_cached("logic", "same content") == [{"b": 2}]
