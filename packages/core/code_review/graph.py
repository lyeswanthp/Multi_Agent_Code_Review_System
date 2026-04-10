"""LangGraph wiring — pre-filter → conditional agents → orchestrator.

Three graph topologies:
  - parallel (remote mode): Independent agents run concurrently, logic waits for syntax.
  - sequential (local mode): Agents run one at a time to share GPU/RAM.
  Both use a pre-filter node to skip unnecessary agents.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from code_review.agents.git_history import run_git_history_agent
from code_review.agents.logic import run_logic_agent
from code_review.agents.orchestrator import run_orchestrator
from code_review.agents.prefilter import run_prefilter
from code_review.agents.security import run_security_agent
from code_review.agents.syntax import run_syntax_agent
from code_review.config import settings
from code_review.state import ReviewState


# --- Conditional routing helpers ---

def _should_run(agent: str, state: ReviewState) -> bool:
    return agent in state.get("agents_to_run", [])


def _route_after_prefilter_parallel(state: ReviewState) -> list[str]:
    """Route to agents that should run (parallel mode)."""
    targets = []
    if _should_run("syntax", state):
        targets.append("syntax_agent")
    if _should_run("security", state):
        targets.append("security_agent")
    if _should_run("git_history", state):
        targets.append("git_history_agent")
    # If syntax won't run but logic should, start logic directly
    if not _should_run("syntax", state) and _should_run("logic", state):
        targets.append("logic_agent")
    return targets or ["orchestrator"]


def _route_after_syntax(state: ReviewState) -> Literal["logic_agent", "orchestrator"]:
    """After syntax: run logic unless syntax found critical issues or logic not needed."""
    if not _should_run("logic", state):
        return "orchestrator"
    if state.get("syntax_has_critical", False):
        return "orchestrator"  # Early termination: skip logic if syntax is critical
    return "logic_agent"


def _route_after_prefilter_sequential(state: ReviewState) -> Literal[
    "syntax_agent", "logic_agent", "security_agent", "git_history_agent", "orchestrator"
]:
    """Route to first agent that should run (sequential mode)."""
    for agent, node in [
        ("syntax", "syntax_agent"),
        ("logic", "logic_agent"),
        ("security", "security_agent"),
        ("git_history", "git_history_agent"),
    ]:
        if _should_run(agent, state):
            return node
    return "orchestrator"


def _route_seq_after(current: str, remaining: list[str], state: ReviewState) -> str:
    """Find the next agent to run in sequential mode, or go to orchestrator."""
    # Early termination: if syntax found critical issues, skip logic
    if current == "syntax" and state.get("syntax_has_critical", False):
        remaining = [a for a in remaining if a != "logic"]

    for agent, node in [(a, f"{a}_agent") for a in remaining]:
        if _should_run(agent, state):
            return node
    return "orchestrator"


def _route_after_syntax_seq(state: ReviewState) -> str:
    return _route_seq_after("syntax", ["logic", "security", "git_history"], state)


def _route_after_logic_seq(state: ReviewState) -> str:
    return _route_seq_after("logic", ["security", "git_history"], state)


def _route_after_security_seq(state: ReviewState) -> str:
    return _route_seq_after("security", ["git_history"], state)


# --- Graph builders ---

def build_parallel_graph():
    """Hybrid fan-out: prefilter → [syntax, security, git_history] parallel.

    Logic waits for syntax (benefits from its findings).
    Early termination: logic skipped if syntax finds critical issues.
    All agents → orchestrator → END.
    """
    builder = StateGraph(ReviewState)
    builder.add_node("prefilter", run_prefilter)
    builder.add_node("syntax_agent", run_syntax_agent)
    builder.add_node("logic_agent", run_logic_agent)
    builder.add_node("security_agent", run_security_agent)
    builder.add_node("git_history_agent", run_git_history_agent)
    builder.add_node("orchestrator", run_orchestrator)

    builder.add_edge(START, "prefilter")
    builder.add_conditional_edges("prefilter", _route_after_prefilter_parallel)

    # Syntax → conditional → logic or orchestrator
    builder.add_conditional_edges("syntax_agent", _route_after_syntax)

    # All other agents → orchestrator
    builder.add_edge("logic_agent", "orchestrator")
    builder.add_edge("security_agent", "orchestrator")
    builder.add_edge("git_history_agent", "orchestrator")

    builder.add_edge("orchestrator", END)
    return builder.compile()


def build_sequential_graph():
    """Chain: prefilter → conditional sequential agents → orchestrator → END.

    Skips agents not needed. Early termination if syntax finds critical issues.
    """
    builder = StateGraph(ReviewState)
    builder.add_node("prefilter", run_prefilter)
    builder.add_node("syntax_agent", run_syntax_agent)
    builder.add_node("logic_agent", run_logic_agent)
    builder.add_node("security_agent", run_security_agent)
    builder.add_node("git_history_agent", run_git_history_agent)
    builder.add_node("orchestrator", run_orchestrator)

    builder.add_edge(START, "prefilter")
    builder.add_conditional_edges("prefilter", _route_after_prefilter_sequential)

    builder.add_conditional_edges("syntax_agent", _route_after_syntax_seq)
    builder.add_conditional_edges("logic_agent", _route_after_logic_seq)
    builder.add_conditional_edges("security_agent", _route_after_security_seq)
    builder.add_edge("git_history_agent", "orchestrator")

    builder.add_edge("orchestrator", END)
    return builder.compile()


def build_review_graph():
    """Pick topology based on LLM_MODE.
    Local: sequential (single GPU can't serve parallel well).
    Remote: parallel (separate API endpoints).
    """
    if settings.llm_mode == "local":
        return build_sequential_graph()
    return build_parallel_graph()


# Pre-built graph instance — import and invoke
review_graph = build_review_graph()
