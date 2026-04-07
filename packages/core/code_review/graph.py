"""LangGraph wiring — fan-out to 4 agents, fan-in to orchestrator."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from code_review.agents.git_history import run_git_history_agent
from code_review.agents.logic import run_logic_agent
from code_review.agents.orchestrator import run_orchestrator
from code_review.agents.security import run_security_agent
from code_review.agents.syntax import run_syntax_agent
from code_review.state import ReviewState


def build_review_graph() -> StateGraph:
    """Build and compile the review pipeline graph.

    Topology (all agents at equal depth from START):
        START ──┬── syntax_agent ────┐
                ├── logic_agent ─────┤
                ├── security_agent ──┼── orchestrator ── END
                └── git_history_agent┘
    """
    builder = StateGraph(ReviewState)

    # Add agent nodes
    builder.add_node("syntax_agent", run_syntax_agent)
    builder.add_node("logic_agent", run_logic_agent)
    builder.add_node("security_agent", run_security_agent)
    builder.add_node("git_history_agent", run_git_history_agent)
    builder.add_node("orchestrator", run_orchestrator)

    # Fan-out: START → all 4 agents in parallel (equal depth)
    for agent in ["syntax_agent", "logic_agent", "security_agent", "git_history_agent"]:
        builder.add_edge(START, agent)
        builder.add_edge(agent, "orchestrator")

    # Fan-in: orchestrator → END
    builder.add_edge("orchestrator", END)

    return builder.compile()


# Pre-built graph instance — import and invoke
review_graph = build_review_graph()
