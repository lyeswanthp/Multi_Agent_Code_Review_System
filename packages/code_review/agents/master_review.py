"""Master Review Agent — consolidated syntax/logic/security in one pass.

Falls back to individual agents if the master returns invalid or incomplete JSON.
Git history remains separate (different input data).
"""

from __future__ import annotations

import logging
from typing import TypedDict

from code_review.agents.logic import run_logic_agent
from code_review.agents.security import run_security_agent
from code_review.agents.syntax import run_syntax_agent
from code_review.agents.per_file import run_per_file
from code_review.events import agent_telemetry, bus
from code_review.llm_client import extract_json, truncate_content, truncate_system_prompt
from code_review.models import AgentName, Finding, Severity
from code_review.rules.loader import load_rules
from code_review.state import ReviewState

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = """\
Analyze code and return JSON with style/logic/security findings. Each finding needs:
{"severity": "medium", "file": "path", "line": 10, "message": "desc", "suggestion": "fix"}
Return: {"style": [], "logic": [], "security": []}
"""


class _FindingsDict(TypedDict):
    style: list[dict]
    logic: list[dict]
    security: list[dict]


def _get_system_prompt() -> str:
    rules = load_rules()
    rule = rules.get("master")
    return rule.body if (rule and rule.body) else FALLBACK_PROMPT


def _parse_master_result(result: _FindingsDict, default_file: str) -> list[Finding]:
    """Convert master agent's categorized output into flat Finding list."""
    findings = []
    cat_map = {
        "style": "style",
        "logic": "logic",
        "security": "security",
    }

    for category, items in result.items():
        finding_cat = cat_map.get(category, category)
        if not isinstance(items, list):
            continue
        for item in items:
            try:
                findings.append(Finding(
                    severity=Severity(item.get("severity", "medium")),
                    file=item.get("file", default_file) or default_file,
                    line=item.get("line", 0),
                    message=item.get("message", ""),
                    agent=AgentName.MASTER,
                    suggestion=item.get("suggestion", ""),
                    category=finding_cat,
                ))
            except Exception:
                continue
    return findings


async def _run_fallback_agents(state: ReviewState) -> list[Finding]:
    """Run individual agents when master fails."""
    logger.info("Master agent failed, falling back to individual agents")
    all_findings = []

    try:
        syntax_result = await run_syntax_agent(state)
        all_findings.extend(syntax_result.get("findings", []))
    except Exception as e:
        logger.warning("Syntax agent fallback failed: %s", e)

    try:
        logic_result = await run_logic_agent(state)
        all_findings.extend(logic_result.get("findings", []))
    except Exception as e:
        logger.warning("Logic agent fallback failed: %s", e)

    try:
        security_result = await run_security_agent(state)
        all_findings.extend(security_result.get("findings", []))
    except Exception as e:
        logger.warning("Security agent fallback failed: %s", e)

    return all_findings


@agent_telemetry("master")
async def run_master_agent(state: ReviewState) -> dict:
    """Run consolidated master agent, falling back to individual agents on failure."""
    from code_review.llm_client import call_agent

    focused_contents = state.get("focused_contents", {})
    file_contents = state.get("file_contents", {})
    diff_context = state.get("diff_context", {})
    external_skeletons = state.get("external_skeletons", {})
    call_chain_text = state.get("call_chain_text", "")
    lsp_context = state.get("lsp_context", {})
    linter_findings = state.get("linter_findings", [])
    semgrep_findings = state.get("semgrep_findings", [])
    bandit_findings = state.get("bandit_findings", [])

    contents = focused_contents if focused_contents else file_contents
    if not contents:
        return {"findings": [], "fallback_used": False}

    # Build file prompts with full context
    files_with_context: dict[str, str] = {}
    for filepath, content in contents.items():
        parts = []

        # SAST findings context
        sast_parts = []
        for item in semgrep_findings + bandit_findings:
            if item.get("file") == filepath or item.get("filename") == filepath:
                sast_parts.append(f"  - {item}")
        if sast_parts:
            parts.append("## SAST Findings:\n" + "\n".join(sast_parts))

        # Linter findings context
        linter_parts = []
        for item in linter_findings:
            if item.get("file") == filepath or item.get("filename") == filepath:
                linter_parts.append(f"  - {item}")
        if linter_parts:
            parts.append("## Linter Findings:\n" + "\n".join(linter_parts))

        # Call chain context
        if call_chain_text:
            parts.append(call_chain_text)

        # External skeletons
        if external_skeletons:
            skel_parts = [f"### {imp}\n{skel}" for imp, skel in external_skeletons.items()]
            if skel_parts:
                parts.append("## External Dependencies:\n" + "\n\n".join(skel_parts))

        # LSP type context
        if filepath in lsp_context:
            from code_review.models import LSPTypeInfo
            lsp_info = LSPTypeInfo(**lsp_context[filepath])
            lsp_str = lsp_info.to_context_str()
            if lsp_str:
                parts.append(lsp_str)

        # Diff context
        dc = diff_context.get(filepath)
        if dc and dc.get("diff"):
            parts.append(f"## Changes:\n```diff\n{dc['diff']}\n```")

        parts.append(f"## Code:\n{content}")
        files_with_context[filepath] = "\n\n".join(parts)

    bus.emit("agent.files", agent="master",
             files=sorted(files_with_context.keys()),
             chars={f: len(c) for f, c in files_with_context.items()})

    # Process each file with master agent
    all_findings: list[Finding] = []
    fallback_used = False
    sys_prompt = truncate_system_prompt(_get_system_prompt())
    file_list = sorted(files_with_context.items())

    for idx, (filepath, content) in enumerate(file_list, 1):
        if not content.strip():
            bus.emit("agent.file.skip", agent="master", file=filepath, reason="empty")
            continue

        user_msg = truncate_content(content)

        bus.emit("agent.file.start", agent="master", file=filepath,
                 index=idx, total=len(file_list), chars=len(content))

        try:
            response = await call_agent(
                AgentName.MASTER,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg},
                ],
            )

            if not response.strip():
                logger.warning("Master agent empty response for %s", filepath)
                response = await call_agent(
                    AgentName.MASTER,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                )

            if not response.strip():
                bus.emit("agent.file.done", agent="master", file=filepath,
                         findings=0, error="empty response")
                continue

            result = extract_json(response)

            if not isinstance(result, dict):
                logger.warning("Master agent returned non-dict for %s: %s", filepath, type(result).__name__)
                # Fallback for this file
                fallback_used = True
                continue

            findings = _parse_master_result(result, filepath)
            all_findings.extend(findings)

            if findings:
                for f in findings:
                    bus.emit("agent.finding", finding=f.model_dump())

            bus.emit("agent.file.done", agent="master", file=filepath,
                     findings=len(findings), categories=result.keys())

        except Exception as e:
            logger.error("Master agent failed for %s: %s", filepath, e)
            fallback_used = True

    if fallback_used:
        logger.info("Master had failures, running individual agents for complete coverage")
        fallback_findings = await _run_fallback_agents(state)
        all_findings.extend(fallback_findings)

    return {"findings": all_findings, "fallback_used": fallback_used}