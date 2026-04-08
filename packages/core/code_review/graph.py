"""LangGraph wiring — fan-out to 4 agents, fan-in to orchestrator.

Two graph topologies:
  - parallel (remote mode): All 4 agents run concurrently, different providers.
  - sequential (local mode): Agents run one at a time to share GPU/RAM.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from code_review.agents.git_history import run_git_history_agent
from code_review.agents.logic import run_logic_agent
from code_review.agents.orchestrator import run_orchestrator
from code_review.agents.security import run_security_agent
from code_review.agents.syntax import run_syntax_agent
from code_review.config import settings
from code_review.state import ReviewState


def build_parallel_graph():
    """Fan-out: START → 4 agents in parallel → orchestrator → END."""
    builder = StateGraph(ReviewState)
    builder.add_node("syntax_agent", run_syntax_agent)
    builder.add_node("logic_agent", run_logic_agent)
    builder.add_node("security_agent", run_security_agent)
    builder.add_node("git_history_agent", run_git_history_agent)
    builder.add_node("orchestrator", run_orchestrator)

    for agent in ["syntax_agent", "logic_agent", "security_agent", "git_history_agent"]:
        builder.add_edge(START, agent)
        builder.add_edge(agent, "orchestrator")

    builder.add_edge("orchestrator", END)
    return builder.compile()


def build_sequential_graph():
    """Chain: START → syntax → logic → security → git_history → orchestrator → END."""
    builder = StateGraph(ReviewState)
    builder.add_node("syntax_agent", run_syntax_agent)
    builder.add_node("logic_agent", run_logic_agent)
    builder.add_node("security_agent", run_security_agent)
    builder.add_node("git_history_agent", run_git_history_agent)
    builder.add_node("orchestrator", run_orchestrator)

    builder.add_edge(START, "syntax_agent")
    builder.add_edge("syntax_agent", "logic_agent")
    builder.add_edge("logic_agent", "security_agent")
    builder.add_edge("security_agent", "git_history_agent")
    builder.add_edge("git_history_agent", "orchestrator")
    builder.add_edge("orchestrator", END)
    return builder.compile()


def build_review_graph():
    """Pick topology based on LLM_MODE."""
    if settings.llm_mode == "local":
        return build_sequential_graph()
    return build_parallel_graph()


# Pre-built graph instance — import and invoke
review_graph = build_review_graph()
