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
from code_review.agents.master_review import run_master_agent
from code_review.agents.orchestrator import run_orchestrator
from code_review.agents.prefilter import run_prefilter
from code_review.config import settings
from code_review.state import ReviewState


# --- Conditional routing helpers ---

def _should_run(agent: str, state: ReviewState) -> bool:
    return agent in state.get("agents_to_run", [])


def _route_after_prefilter_parallel(state: ReviewState) -> list[str]:
    """Route to agents that should run (parallel mode).

    Master agent handles syntax/logic/security in one pass.
    Git history runs separately (different input data).
    """
    targets = []
    if _should_run("master", state):
        targets.append("master_agent")
    if _should_run("git_history", state):
        targets.append("git_history_agent")
    return targets or ["orchestrator"]


def _route_after_master(state: ReviewState) -> Literal["git_history_agent", "orchestrator"]:
    """After master: run git_history if needed, then orchestrator."""
    if _should_run("git_history", state):
        return "git_history_agent"
    return "orchestrator"


def _route_after_prefilter_sequential(state: ReviewState) -> Literal[
    "master_agent", "git_history_agent", "orchestrator"
]:
    """Route to first agent that should run (sequential mode)."""
    for agent, node in [
        ("master", "master_agent"),
        ("git_history", "git_history_agent"),
    ]:
        if _should_run(agent, state):
            return node
    return "orchestrator"


def _route_after_master_seq(state: ReviewState) -> str:
    """After master: run git_history if needed, then orchestrator."""
    if _should_run("git_history", state):
        return "git_history_agent"
    return "orchestrator"


def _route_after_git_history_seq(state: ReviewState) -> str:
    return "orchestrator"


def _route_after_git_history(state: ReviewState) -> str:
    return "orchestrator"


# --- Graph builders ---

def build_parallel_graph():
    """Hybrid fan-out: prefilter → [master, git_history] parallel.

    Master agent handles syntax/logic/security in one pass.
    Git history runs separately (different input: overlap diffs).
    All agents → orchestrator → END.
    """
    builder = StateGraph(ReviewState)
    builder.add_node("prefilter", run_prefilter)
    builder.add_node("master_agent", run_master_agent)
    builder.add_node("git_history_agent", run_git_history_agent)
    builder.add_node("orchestrator", run_orchestrator)

    builder.add_edge(START, "prefilter")
    builder.add_conditional_edges("prefilter", _route_after_prefilter_parallel)

    # Master → conditional → git_history or orchestrator
    builder.add_conditional_edges("master_agent", _route_after_master)

    # Git history → orchestrator
    builder.add_edge("git_history_agent", "orchestrator")

    builder.add_edge("orchestrator", END)
    return builder.compile()


def build_sequential_graph():
    """Chain: prefilter → master → git_history → orchestrator → END.

    Master handles syntax/logic/security in one pass.
    Git history runs after if enabled.
    """
    builder = StateGraph(ReviewState)
    builder.add_node("prefilter", run_prefilter)
    builder.add_node("master_agent", run_master_agent)
    builder.add_node("git_history_agent", run_git_history_agent)
    builder.add_node("orchestrator", run_orchestrator)

    builder.add_edge(START, "prefilter")
    builder.add_conditional_edges("prefilter", _route_after_prefilter_sequential)

    builder.add_conditional_edges("master_agent", _route_after_master_seq)
    builder.add_conditional_edges("git_history_agent", _route_after_git_history_seq)

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
