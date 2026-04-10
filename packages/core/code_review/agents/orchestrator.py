"""Orchestrator Agent — synthesizes, deduplicates, and ranks all agent findings.

Provider: NVIDIA NIM | Model: nvidia/llama-3_3-nemotron-super-49b-v1
"""

from __future__ import annotations

import json
import logging

from code_review.llm_client import call_agent, extract_json
from code_review.models import AgentName, Finding, Severity
from code_review.rules.loader import load_rules
from code_review.state import ReviewState

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = """\
You are the orchestrator agent for a multi-agent code review system. You receive findings from four specialist agents (Syntax, Logic, Security, Git History) and produce a final unified review.

Your job:
1. DEDUPLICATE — if multiple agents flagged the same issue, merge into one finding with the highest severity.
2. RANK — order findings by impact: critical > high > medium > low.
3. SYNTHESIZE — write a concise executive summary (2-4 sentences) covering the overall code quality.
4. FILTER — remove findings that contradict each other (explain the conflict briefly).

Return ONLY a JSON object:
{"findings": [{"severity": "critical|high|medium|low", "file": "path", "line": 10, "message": "Description", "suggestion": "Fix", "category": "security|logic|style|git_history"}], "summary": "Executive summary."}
"""


def _get_system_prompt() -> str:
    rules = load_rules()
    rule = rules.get("orchestrator")
    if rule and rule.body:
        return rule.body
    return FALLBACK_PROMPT


def _deterministic_summary(findings: list[Finding]) -> str:
    """Build a plain-text summary without an LLM call."""
    from collections import Counter
    counts = Counter(f.severity.value for f in findings)
    files = len({f.file for f in findings})
    agents = sorted({f.agent.value for f in findings})
    parts = [f"{counts.get(s, 0)} {s}" for s in ("critical", "high", "medium", "low") if counts.get(s)]
    return (
        f"Found {len(findings)} issue(s) across {files} file(s): {', '.join(parts)}. "
        f"Agents that contributed: {', '.join(agents)}."
    )


async def run_orchestrator(state: ReviewState) -> dict:
    """Synthesize all agent findings into a final review."""
    from code_review.config import settings
    findings = state["findings"]

    if not findings:
        return {"findings": [], "summary": "No issues found. Code looks clean."}

    # Local mode: skip the LLM call — local models can't reliably produce the
    # nested JSON this prompt requires, causing findings to be silently dropped.
    if settings.llm_mode == "local":
        return {"summary": _deterministic_summary(findings)}

    serialized = [f.model_dump() if hasattr(f, "model_dump") else f for f in findings]
    user_msg = f"Agent findings to synthesize:\n{json.dumps(serialized, indent=2, default=str)}"

    response = await call_agent(
        AgentName.ORCHESTRATOR,
        messages=[
            {"role": "system", "content": _get_system_prompt()},
            {"role": "user", "content": user_msg},
        ],
    )

    if not response:
        return {"summary": _deterministic_summary(findings)}

    try:
        result = extract_json(response)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Orchestrator returned non-JSON response")
        return {"summary": _deterministic_summary(findings)}

    if not isinstance(result, dict):
        logger.warning("Orchestrator returned a JSON array instead of object")
        return {"summary": _deterministic_summary(findings)}

    # Build a lookup from the raw findings to restore original agent labels
    _raw: dict[tuple, AgentName] = {}
    for raw_f in findings:
        key = (raw_f.file, raw_f.line, raw_f.category)
        _raw[key] = raw_f.agent

    # Parse orchestrator findings — preserve original agent attribution where possible
    orchestrated_findings = []
    for item in result.get("findings", []):
        category = item.get("category", "")
        line = item.get("line", 0)
        file_ = item.get("file", "")
        original_agent = _raw.get((file_, line, category), AgentName.ORCHESTRATOR)
        orchestrated_findings.append(Finding(
            severity=Severity(item.get("severity", "medium")),
            file=file_,
            line=line,
            message=item.get("message", ""),
            agent=original_agent,
            suggestion=item.get("suggestion", ""),
            category=category,
        ))

    output = {"summary": result.get("summary", _deterministic_summary(findings))}
    if orchestrated_findings:
        output["findings"] = orchestrated_findings
    return output
