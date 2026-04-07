"""Tests for the LangGraph review pipeline — mock all LLM calls."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from code_review.graph import build_review_graph


def _make_state():
    return {
        "raw_diff": "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new",
        "changed_files": ["test.py"],
        "overlap_files": [],
        "file_contents": {"test.py": "print('new')"},
        "import_context": {},
        "linter_findings": [{"code": "W001", "file": "test.py", "line": 1}],
        "semgrep_findings": [],
        "bandit_findings": [],
        "overlap_diffs": {},
        "findings": [],
        "summary": "",
    }


def _mock_agent_response(findings_data):
    return json.dumps(findings_data)


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

        with patch("code_review.agents.syntax.call_agent", side_effect=mock_call_agent), \
             patch("code_review.agents.logic.call_agent", side_effect=mock_call_agent), \
             patch("code_review.agents.security.call_agent", side_effect=mock_call_agent), \
             patch("code_review.agents.git_history.call_agent", side_effect=mock_call_agent), \
             patch("code_review.agents.orchestrator.call_agent", side_effect=mock_call_agent):
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

        with patch("code_review.agents.syntax.call_agent", side_effect=mock_call_agent), \
             patch("code_review.agents.logic.call_agent", side_effect=mock_call_agent), \
             patch("code_review.agents.security.call_agent", side_effect=mock_call_agent), \
             patch("code_review.agents.git_history.call_agent", side_effect=mock_call_agent), \
             patch("code_review.agents.orchestrator.call_agent", side_effect=mock_call_agent):
            result = await graph.ainvoke(_make_state())

        assert "clean" in result["summary"].lower()
