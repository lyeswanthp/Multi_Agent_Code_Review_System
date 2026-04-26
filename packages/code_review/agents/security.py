"""Security Agent — finds vulnerabilities per file."""

from __future__ import annotations

import json
import logging

from code_review.agents.per_file import run_per_file
from code_review.events import agent_telemetry, bus
from code_review.models import AgentName
from code_review.rules.loader import load_rules
from code_review.state import ReviewState

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = """\
Find security vulnerabilities in this code. You will see a DIFF section showing what changed (- = old, + = new) followed by the current code with >>> markers.
Compare old vs new for regressions. Check: injection, hardcoded secrets, eval/exec, path traversal, insecure deserialization.
Return ONLY JSON: [{"severity":"critical|high|medium|low","file":"path","line":10,"message":"vuln","suggestion":"fix"}]
Empty if clean: []
"""


def _get_system_prompt() -> str:
    rules = load_rules()
    rule = rules.get("security")
    return rule.body if (rule and rule.body) else FALLBACK_PROMPT


@agent_telemetry("security")
async def run_security_agent(state: ReviewState) -> dict:
    """Triage each file individually for security issues."""
    semgrep = state["semgrep_findings"]
    bandit = state["bandit_findings"]
    focused_contents = state["focused_contents"]
    file_contents = state["file_contents"]

    diff_context = state.get("diff_context", {})
    external_skeletons = state.get("external_skeletons", {})
    call_chain_text = state.get("call_chain_text", "")
    lsp_context = state.get("lsp_context", {})

    contents = focused_contents if focused_contents else file_contents
    if not semgrep and not bandit and not contents:
        return {"findings": []}

    bus.emit("agent.files", agent="security", files=sorted(contents.keys()),
             chars={f: len(c) for f, c in contents.items()})

    # Build per-file SAST context
    sast_by_file: dict[str, str] = {}
    for item in semgrep + bandit:
        fpath = item.get("file", item.get("filename", ""))
        if fpath:
            sast_by_file.setdefault(fpath, "")
            sast_by_file[fpath] += json.dumps(item, default=str) + "\n"

    # For files with SAST findings + diff context, prepend as extra context
    files_with_context = {}
    for filepath, content in contents.items():
        parts = []
        extra = sast_by_file.get(filepath, "")
        if extra:
            parts.append(f"## SAST findings for this file:\n{extra}")

        if call_chain_text:
            parts.append(call_chain_text)

        relevant_skels = []
        for imp, skel in external_skeletons.items():
            relevant_skels.append(f"### {imp}\n{skel}")
        if relevant_skels:
            parts.append("## External Dependencies (Skeletons):\n" + "\n\n".join(relevant_skels) + "\n")

        # Add LSP type context if available
        if filepath in lsp_context:
            from code_review.models import LSPTypeInfo
            lsp_info = LSPTypeInfo(**lsp_context[filepath])
            lsp_str = lsp_info.to_context_str()
            if lsp_str:
                parts.append(lsp_str)

        dc = diff_context.get(filepath)
        if dc and dc.get("diff"):
            parts.append(f"## Changes (old → new):\n```diff\n{dc['diff']}\n```")

        parts.append(content)
        files_with_context[filepath] = "\n\n".join(parts)

    all_findings = await run_per_file(
        agent_name="security",
        agent_enum=AgentName.SECURITY,
        system_prompt=_get_system_prompt(),
        files=files_with_context,
        category="security",
    )

    return {"findings": all_findings}
