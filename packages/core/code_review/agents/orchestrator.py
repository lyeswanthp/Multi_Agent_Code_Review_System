"""Orchestrator Agent — synthesizes, deduplicates, and ranks all agent findings.

Provider: NVIDIA NIM | Model: nvidia/llama-3_3-nemotron-super-49b-v1
"""

from __future__ import annotations

import json
import logging

from code_review.llm_client import call_agent
from code_review.models import AgentName, Finding, Severity
from code_review.state import ReviewState

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the orchestrator agent for a multi-agent code review system. You receive findings from four specialist agents (Syntax, Logic, Security, Git History) and produce a final unified review.

Your job:
1. DEDUPLICATE — if multiple agents flagged the same issue, merge into one finding with the highest severity.
2. RANK — order findings by impact: critical > high > medium > low.
3. SYNTHESIZE — write a concise executive summary (2-4 sentences) covering the overall code quality.
4. FILTER — remove findings that contradict each other (e.g., one agent says it's safe, another says it's not — explain the conflict briefly).

Output format — return a JSON object:
{
  "findings": [
    {
      "severity": "critical|high|medium|low",
      "file": "path/to/file.py",
      "line": 10,
      "message": "Unified description",
      "suggestion": "Best fix from all agents",
      "category": "security|logic|style|git_history"
    }
  ],
  "summary": "Executive summary of the review."
}

Return ONLY the JSON object, no markdown fences, no extra text.
"""


async def run_orchestrator(state: ReviewState) -> dict:
    """Synthesize all agent findings into a final review."""
    findings = state["findings"]

    if not findings:
        return {
            "findings": [],
            "summary": "No issues found. Code looks clean.",
        }

    serialized = [f.model_dump() if hasattr(f, "model_dump") else f for f in findings]
    user_msg = f"Agent findings to synthesize:\n{json.dumps(serialized, indent=2, default=str)}"

    response = await call_agent(
        AgentName.ORCHESTRATOR,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )

    if not response:
        return {"summary": "Orchestrator unavailable. Raw findings returned as-is."}

    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        logger.warning("Orchestrator returned non-JSON response")
        return {"summary": "Orchestrator produced unstructured output. Raw findings returned."}

    # Parse orchestrator findings
    orchestrated_findings = []
    for item in result.get("findings", []):
        orchestrated_findings.append(Finding(
            severity=Severity(item.get("severity", "medium")),
            file=item.get("file", ""),
            line=item.get("line", 0),
            message=item.get("message", ""),
            agent=AgentName.ORCHESTRATOR,
            suggestion=item.get("suggestion", ""),
            category=item.get("category", ""),
        ))

    output = {"summary": result.get("summary", "")}
    if orchestrated_findings:
        output["findings"] = orchestrated_findings
    return output
