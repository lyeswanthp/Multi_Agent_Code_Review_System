"""Tests for the LangGraph review pipeline — mock all LLM calls."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from code_review.graph import build_review_graph


def _make_state(**overrides):
    base = {
        "raw_diff": "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new",
        "changed_files": ["test.py"],
        "overlap_files": [],
        "file_contents": {"test.py": "print('new')"},
        "focused_contents": {"test.py": "print('new')"},
        "import_context": {},
        "linter_findings": [{"code": "W001", "file": "test.py", "line": 1}],
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


def _mock_agent_response(findings_data):
    return json.dumps(findings_data)


def _patch_all_agents(mock_fn):
    """Return a context manager that patches all agent call_agent functions."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with patch("code_review.agents.syntax.call_agent", side_effect=mock_fn), \
             patch("code_review.agents.logic.call_agent", side_effect=mock_fn), \
             patch("code_review.agents.security.call_agent", side_effect=mock_fn), \
             patch("code_review.agents.git_history.call_agent", side_effect=mock_fn), \
             patch("code_review.agents.orchestrator.call_agent", side_effect=mock_fn):
            yield
    return _ctx()


class TestReviewGraph:
    @pytest.mark.asyncio
    async def test_graph_compiles(self):
        graph = build_review_graph()
        assert graph is not None

    @pytest.mark.asyncio
    async def test_full_pipeline_with_mocks(self):
        syntax_resp = json.dumps([{
            "severity": "medium", "file": "test.py", "line": 1,
            "message": "Style issue", "suggestion": "Fix style",
        }])
        logic_resp = json.dumps([{
            "severity": "high", "file": "test.py", "line": 1,
            "message": "Logic bug", "suggestion": "Fix logic",
        }])
        security_resp = json.dumps([])
        git_resp = json.dumps([])
        orchestrator_resp = json.dumps({
            "findings": [
                {"severity": "high", "file": "test.py", "line": 1,
                 "message": "Logic bug", "suggestion": "Fix logic", "category": "logic"},
                {"severity": "medium", "file": "test.py", "line": 1,
                 "message": "Style issue", "suggestion": "Fix style", "category": "style"},
            ],
            "summary": "Found 1 logic bug and 1 style issue.",
        })

        call_responses = {
            "syntax": syntax_resp,
            "logic": logic_resp,
            "security": security_resp,
            "git_history": git_resp,
            "orchestrator": orchestrator_resp,
        }

        async def mock_call_agent(agent, messages, temperature=0.1):
            agent_name = agent.value if hasattr(agent, "value") else agent
            return call_responses.get(agent_name, "[]")

        graph = build_review_graph()

        with _patch_all_agents(mock_call_agent):
            result = await graph.ainvoke(_make_state())

        assert result["summary"] == "Found 1 logic bug and 1 style issue."
        assert len(result["findings"]) > 0

    @pytest.mark.asyncio
    async def test_git_history_skips_when_no_overlap(self):
        """Git history agent should produce no findings when overlap is empty."""
        empty_resp = json.dumps([])
        orchestrator_resp = json.dumps({"findings": [], "summary": "Clean."})

        async def mock_call_agent(agent, messages, temperature=0.1):
            agent_name = agent.value if hasattr(agent, "value") else agent
            if agent_name == "orchestrator":
                return orchestrator_resp
            return empty_resp

        graph = build_review_graph()

        with _patch_all_agents(mock_call_agent):
            result = await graph.ainvoke(_make_state())

        assert "clean" in result["summary"].lower()

    @pytest.mark.asyncio
    async def test_skips_agents_when_no_data(self):
        """Pre-filter should skip agents when no relevant data exists."""
        orchestrator_resp = json.dumps({"findings": [], "summary": "Nothing to review."})

        async def mock_call_agent(agent, messages, temperature=0.1):
            agent_name = agent.value if hasattr(agent, "value") else agent
            if agent_name == "orchestrator":
                return orchestrator_resp
            # If any non-orchestrator agent is called, fail the test
            raise AssertionError(f"Agent {agent_name} should not have been called")

        graph = build_review_graph()

        # Empty state — no linter findings, no diff, no files, no overlap
        state = _make_state(
            raw_diff="",
            changed_files=[],
            file_contents={},
            focused_contents={},
            linter_findings=[],
            semgrep_findings=[],
            bandit_findings=[],
            overlap_files=[],
        )

        with _patch_all_agents(mock_call_agent):
            result = await graph.ainvoke(state)

        assert "no issues" in result["summary"].lower() or "clean" in result["summary"].lower()

    @pytest.mark.asyncio
    async def test_skips_security_for_non_code_files(self):
        """Pre-filter should skip security agent for non-code files like markdown."""
        called_agents = []

        async def mock_call_agent(agent, messages, temperature=0.1):
            agent_name = agent.value if hasattr(agent, "value") else agent
            called_agents.append(agent_name)
            if agent_name == "orchestrator":
                return json.dumps({"findings": [], "summary": "Docs only."})
            return json.dumps([])

        graph = build_review_graph()

        state = _make_state(
            changed_files=["README.md", "docs/guide.txt"],
            linter_findings=[{"code": "W001", "file": "README.md", "line": 1}],
            semgrep_findings=[],
            bandit_findings=[],
            overlap_files=[],
        )

        with _patch_all_agents(mock_call_agent):
            result = await graph.ainvoke(state)

        assert "security" not in called_agents
        assert "git_history" not in called_agents
