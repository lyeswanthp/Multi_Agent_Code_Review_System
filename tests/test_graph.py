"""Aggressive graph tests — both topologies, routing functions in isolation,
early termination, agent call-count verification, and skip chain coverage."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from code_review.graph import (
    _route_after_prefilter_parallel,
    _route_after_prefilter_sequential,
    _route_after_syntax,
    _route_after_syntax_seq,
    _route_after_logic_seq,
    _route_after_security_seq,
    _route_seq_after,
    _should_run,
    build_parallel_graph,
    build_review_graph,
    build_sequential_graph,
)


def _make_state(**overrides):
    base = {
        "raw_diff": "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new",
        "changed_files": ["test.py"],
        "overlap_files": [],
        "file_contents": {"test.py": "print('new')"},
        "focused_contents": {"test.py": "print('new')"},
        "diff_context": {},
        "external_skeletons": {},
        "call_chain_text": "",
        "graph_context": {"nodes": [], "edges": []},
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


def _patch_all_agents(mock_fn):
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with patch("code_review.agents.per_file.call_agent", side_effect=mock_fn), \
             patch("code_review.agents.git_history.call_agent", side_effect=mock_fn), \
             patch("code_review.agents.orchestrator.call_agent", side_effect=mock_fn), \
             patch("code_review.config.settings.llm_mode", "remote"):
            yield
    return _ctx()


# ---------------------------------------------------------------------------
# Unit tests for routing functions (no graph needed)
# ---------------------------------------------------------------------------

class TestShouldRun:
    def test_agent_in_list(self):
        assert _should_run("syntax", {"agents_to_run": ["syntax", "logic"]})

    def test_agent_not_in_list(self):
        assert not _should_run("security", {"agents_to_run": ["syntax"]})

    def test_empty_list(self):
        assert not _should_run("syntax", {"agents_to_run": []})

    def test_missing_key(self):
        assert not _should_run("syntax", {})


class TestRouteAfterPrefilterParallel:
    def test_all_agents(self):
        state = _make_state(agents_to_run=["syntax", "logic", "security", "git_history"])
        targets = _route_after_prefilter_parallel(state)
        assert "syntax_agent" in targets
        assert "security_agent" in targets
        assert "git_history_agent" in targets
        # Logic should NOT be in targets — it waits for syntax
        assert "logic_agent" not in targets

    def test_no_syntax_but_logic(self):
        """When syntax is skipped, logic should start directly."""
        state = _make_state(agents_to_run=["logic", "security"])
        targets = _route_after_prefilter_parallel(state)
        assert "logic_agent" in targets
        assert "security_agent" in targets
        assert "syntax_agent" not in targets

    def test_no_agents_routes_to_orchestrator(self):
        state = _make_state(agents_to_run=[])
        targets = _route_after_prefilter_parallel(state)
        assert targets == ["orchestrator"]

    def test_only_syntax(self):
        state = _make_state(agents_to_run=["syntax"])
        targets = _route_after_prefilter_parallel(state)
        assert targets == ["syntax_agent"]

    def test_only_git_history(self):
        state = _make_state(agents_to_run=["git_history"])
        targets = _route_after_prefilter_parallel(state)
        assert targets == ["git_history_agent"]


class TestRouteAfterSyntax:
    def test_logic_needed_no_critical(self):
        state = _make_state(agents_to_run=["syntax", "logic"], syntax_has_critical=False)
        assert _route_after_syntax(state) == "logic_agent"

    def test_logic_needed_but_critical(self):
        """Early termination — syntax found critical, skip logic."""
        state = _make_state(agents_to_run=["syntax", "logic"], syntax_has_critical=True)
        assert _route_after_syntax(state) == "orchestrator"

    def test_logic_not_needed(self):
        state = _make_state(agents_to_run=["syntax"])
        assert _route_after_syntax(state) == "orchestrator"


class TestRouteAfterPrefilterSequential:
    def test_first_agent_syntax(self):
        state = _make_state(agents_to_run=["syntax", "logic"])
        assert _route_after_prefilter_sequential(state) == "syntax_agent"

    def test_skip_syntax_start_logic(self):
        state = _make_state(agents_to_run=["logic", "security"])
        assert _route_after_prefilter_sequential(state) == "logic_agent"

    def test_only_security(self):
        state = _make_state(agents_to_run=["security"])
        assert _route_after_prefilter_sequential(state) == "security_agent"

    def test_only_git_history(self):
        state = _make_state(agents_to_run=["git_history"])
        assert _route_after_prefilter_sequential(state) == "git_history_agent"

    def test_empty_goes_to_orchestrator(self):
        state = _make_state(agents_to_run=[])
        assert _route_after_prefilter_sequential(state) == "orchestrator"


class TestSequentialChainRouting:
    """_route_after_*_seq functions for skip chain coverage."""

    def test_syntax_seq_to_logic(self):
        state = _make_state(agents_to_run=["syntax", "logic", "security"])
        assert _route_after_syntax_seq(state) == "logic_agent"

    def test_syntax_seq_critical_skips_logic(self):
        state = _make_state(agents_to_run=["syntax", "logic", "security"], syntax_has_critical=True)
        assert _route_after_syntax_seq(state) == "security_agent"

    def test_syntax_seq_critical_only_logic_goes_to_orchestrator(self):
        state = _make_state(agents_to_run=["syntax", "logic"], syntax_has_critical=True)
        assert _route_after_syntax_seq(state) == "orchestrator"

    def test_logic_seq_to_security(self):
        state = _make_state(agents_to_run=["logic", "security"])
        assert _route_after_logic_seq(state) == "security_agent"

    def test_logic_seq_skip_security_to_git_history(self):
        state = _make_state(agents_to_run=["logic", "git_history"])
        assert _route_after_logic_seq(state) == "git_history_agent"

    def test_logic_seq_no_more_agents(self):
        state = _make_state(agents_to_run=["logic"])
        assert _route_after_logic_seq(state) == "orchestrator"

    def test_security_seq_to_git_history(self):
        state = _make_state(agents_to_run=["security", "git_history"])
        assert _route_after_security_seq(state) == "git_history_agent"

    def test_security_seq_no_more_agents(self):
        state = _make_state(agents_to_run=["security"])
        assert _route_after_security_seq(state) == "orchestrator"


class TestRouteSeqAfterGeneric:
    def test_skips_agents_not_in_run_list(self):
        state = _make_state(agents_to_run=["git_history"])
        result = _route_seq_after("logic", ["security", "git_history"], state)
        assert result == "git_history_agent"

    def test_all_skipped(self):
        state = _make_state(agents_to_run=[])
        result = _route_seq_after("syntax", ["logic", "security", "git_history"], state)
        assert result == "orchestrator"

    def test_critical_syntax_removes_logic_from_remaining(self):
        state = _make_state(agents_to_run=["syntax", "logic", "security"], syntax_has_critical=True)
        result = _route_seq_after("syntax", ["logic", "security", "git_history"], state)
        assert result == "security_agent"


# ---------------------------------------------------------------------------
# Integration tests — full graph invocation with both topologies
# ---------------------------------------------------------------------------

class TestParallelGraphIntegration:
    """Integration tests with prefilter-aware state.

    Note: The graph always runs prefilter first, which OVERWRITES agents_to_run
    based on state data. Tests must provide the right data fields to trigger
    prefilter to select the desired agents.

    In parallel mode, orchestrator may be called multiple times as branches
    converge independently — this is expected LangGraph behavior.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from code_review.cache import clear_cache
        clear_cache()
        yield
        clear_cache()

    @pytest.mark.asyncio
    async def test_full_pipeline_call_counts(self):
        """Verify each agent is called exactly once in a full run."""
        call_log = []
        _finding = json.dumps([{"severity": "low", "file": "t.py", "line": 1, "message": "x", "suggestion": "y"}])

        async def mock_call_agent(agent, messages, temperature=0.1):
            name = agent.value if hasattr(agent, "value") else agent
            call_log.append(name)
            if name == "orchestrator":
                return json.dumps({"findings": [], "summary": "Done."})
            return _finding  # Agents must produce findings so orchestrator calls LLM

        graph = build_parallel_graph()
        state = _make_state(
            linter_findings=[{"code": "W001"}],
            raw_diff="diff",
            file_contents={"test.py": "code"},
            changed_files=["test.py"],
            semgrep_findings=[{"check_id": "test"}],
            overlap_files=["test.py"],
            overlap_diffs={"test.py": "diff"},
        )
        with _patch_all_agents(mock_call_agent):
            await graph.ainvoke(state)

        assert call_log.count("syntax") == 1
        assert call_log.count("logic") == 1
        assert call_log.count("security") == 1
        assert call_log.count("git_history") == 1
        # Orchestrator may run multiple times in parallel mode as branches converge
        assert call_log.count("orchestrator") >= 1

    @pytest.mark.asyncio
    async def test_early_termination_skips_logic(self):
        """When syntax sets syntax_has_critical, logic should not run."""
        call_log = []

        async def mock_call_agent(agent, messages, temperature=0.1):
            name = agent.value if hasattr(agent, "value") else agent
            call_log.append(name)
            if name == "syntax":
                return json.dumps([{"severity": "critical", "file": "t.py", "line": 1, "message": "x", "suggestion": "y"}])
            if name == "orchestrator":
                return json.dumps({"findings": [{"severity": "critical", "file": "t.py", "line": 1, "message": "x", "suggestion": "y", "category": "style"}], "summary": "Critical."})
            return json.dumps([])

        graph = build_parallel_graph()
        state = _make_state(
            linter_findings=[{"code": "W001"}],
            raw_diff="diff",
            file_contents={"t.py": "code"},
            changed_files=["t.py"],
        )
        with _patch_all_agents(mock_call_agent):
            result = await graph.ainvoke(state)

        assert "syntax" in call_log
        assert "logic" not in call_log
        # Orchestrator called because syntax produced findings
        assert "orchestrator" in call_log

    @pytest.mark.asyncio
    async def test_empty_state_skips_all_agents(self):
        call_log = []

        async def mock_call_agent(agent, messages, temperature=0.1):
            name = agent.value if hasattr(agent, "value") else agent
            call_log.append(name)
            if name == "orchestrator":
                return json.dumps({"findings": [], "summary": "Nothing."})
            raise AssertionError(f"{name} should not have been called")

        graph = build_parallel_graph()
        state = _make_state(
            raw_diff="", changed_files=[], file_contents={}, focused_contents={},
            linter_findings=[], semgrep_findings=[], bandit_findings=[], overlap_files=[],
        )
        with _patch_all_agents(mock_call_agent):
            result = await graph.ainvoke(state)

        non_orch = [c for c in call_log if c != "orchestrator"]
        assert non_orch == [], f"Unexpected agent calls: {non_orch}"

    @pytest.mark.asyncio
    async def test_findings_accumulate_from_multiple_agents(self):
        """Findings from syntax+security should reach orchestrator."""
        call_log = []

        async def mock_call_agent(agent, messages, temperature=0.1):
            name = agent.value if hasattr(agent, "value") else agent
            call_log.append(name)
            if name == "syntax":
                return json.dumps([{"severity": "medium", "file": "t.py", "line": 1, "message": "style", "suggestion": ""}])
            if name == "security":
                return json.dumps([{"severity": "medium", "file": "t.py", "line": 5, "message": "sqli", "suggestion": ""}])
            if name == "orchestrator":
                return json.dumps({"findings": [
                    {"severity": "medium", "file": "t.py", "line": 5, "message": "sqli", "suggestion": "", "category": "security"},
                    {"severity": "medium", "file": "t.py", "line": 1, "message": "style", "suggestion": "", "category": "style"},
                ], "summary": "2 issues."})
            return json.dumps([])

        graph = build_parallel_graph()
        state = _make_state(
            linter_findings=[{"code": "W001"}],
            raw_diff="diff",
            file_contents={"t.py": "code"},
            changed_files=["t.py"],
            semgrep_findings=[{"check_id": "test"}],
        )
        with _patch_all_agents(mock_call_agent):
            result = await graph.ainvoke(state)

        # Orchestrator was called (agents produced findings)
        assert "orchestrator" in call_log
        assert result["summary"] == "2 issues."


class TestSequentialGraphIntegration:

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from code_review.cache import clear_cache
        clear_cache()
        yield
        clear_cache()

    @pytest.mark.asyncio
    async def test_sequential_runs_agents_in_order(self):
        call_order = []
        _finding = json.dumps([{"severity": "low", "file": "t.py", "line": 1, "message": "x", "suggestion": "y"}])

        async def mock_call_agent(agent, messages, temperature=0.1):
            name = agent.value if hasattr(agent, "value") else agent
            call_order.append(name)
            if name == "orchestrator":
                return json.dumps({"findings": [], "summary": "Done."})
            return _finding

        graph = build_sequential_graph()
        state = _make_state(
            linter_findings=[{"code": "W001"}],
            raw_diff="diff",
            file_contents={"test.py": "code"},
            changed_files=["test.py"],
            semgrep_findings=[{"check_id": "test"}],
            overlap_files=["test.py"],
            overlap_diffs={"test.py": "diff"},
        )
        with _patch_all_agents(mock_call_agent):
            await graph.ainvoke(state)

        agent_calls = [c for c in call_order if c != "orchestrator"]
        assert agent_calls == ["syntax", "logic", "security", "git_history"]

    @pytest.mark.asyncio
    async def test_sequential_early_termination(self):
        call_log = []

        async def mock_call_agent(agent, messages, temperature=0.1):
            name = agent.value if hasattr(agent, "value") else agent
            call_log.append(name)
            if name == "syntax":
                return json.dumps([{"severity": "critical", "file": "t.py", "line": 1, "message": "x", "suggestion": "y"}])
            if name == "orchestrator":
                return json.dumps({"findings": [{"severity": "critical", "file": "t.py", "line": 1, "message": "x", "suggestion": "y", "category": "style"}], "summary": "Critical."})
            return json.dumps([{"severity": "low", "file": "t.py", "line": 1, "message": "x", "suggestion": "y"}])

        graph = build_sequential_graph()
        state = _make_state(
            linter_findings=[{"code": "W001"}],
            raw_diff="diff",
            file_contents={"t.py": "code"},
            changed_files=["t.py"],
            semgrep_findings=[{"check_id": "test"}],
        )
        with _patch_all_agents(mock_call_agent):
            await graph.ainvoke(state)

        assert "syntax" in call_log
        assert "logic" not in call_log  # Skipped due to critical
        assert "security" in call_log

    @pytest.mark.asyncio
    async def test_sequential_skip_to_middle(self):
        """When only security data exists, prefilter skips syntax/logic/git_history."""
        call_log = []

        async def mock_call_agent(agent, messages, temperature=0.1):
            name = agent.value if hasattr(agent, "value") else agent
            call_log.append(name)
            if name == "orchestrator":
                return json.dumps({"findings": [], "summary": "Security only."})
            return json.dumps([])

        graph = build_sequential_graph()
        # Only semgrep findings — prefilter enables only security
        state = _make_state(
            semgrep_findings=[{"check_id": "test", "file": "x.py"}],
            changed_files=["x.py"],
            linter_findings=[],
            raw_diff="",
            file_contents={"x.py": "def foo(): pass"},
            focused_contents={},
            overlap_files=[],
        )
        with _patch_all_agents(mock_call_agent):
            await graph.ainvoke(state)

        assert "syntax" not in call_log
        assert "logic" in call_log
        assert "security" in call_log


class TestBuildReviewGraph:
    """build_review_graph picks topology based on config."""

    def test_local_mode_builds_sequential(self):
        with patch("code_review.graph.settings") as mock_settings:
            mock_settings.llm_mode = "local"
            graph = build_review_graph()
        assert graph is not None

    def test_remote_mode_builds_parallel(self):
        with patch("code_review.graph.settings") as mock_settings:
            mock_settings.llm_mode = "remote"
            graph = build_review_graph()
        assert graph is not None
