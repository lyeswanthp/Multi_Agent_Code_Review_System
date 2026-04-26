"""Logic Agent — finds bugs per file using diff hunks."""

from __future__ import annotations

import logging

from code_review.agents.per_file import run_per_file
from code_review.events import agent_telemetry, bus
from code_review.models import AgentName
from code_review.rules.loader import load_rules
from code_review.state import ReviewState

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = """\
Find bugs in this code. You will see a DIFF section showing what changed (- = old, + = new) followed by the current code with >>> markers on changed lines.
Compare old vs new logic carefully. Check: regressions, off-by-one, null/None, wrong conditions, dead code, exception handling.
Return ONLY JSON: [{"severity":"critical|high|medium|low","file":"path","line":10,"message":"bug","suggestion":"fix"}]
Empty if clean: []
"""


def _get_system_prompt() -> str:
    rules = load_rules()
    rule = rules.get("logic")
    return rule.body if (rule and rule.body) else FALLBACK_PROMPT


@agent_telemetry("logic")
async def run_logic_agent(state: ReviewState) -> dict:
    """Analyze each file individually for logic errors."""
    focused_contents = state["focused_contents"]
    file_contents = state["file_contents"]

    diff_context = state.get("diff_context", {})
    external_skeletons = state.get("external_skeletons", {})
    call_chain_text = state.get("call_chain_text", "")
    lsp_context = state.get("lsp_context", {})

    contents = focused_contents if focused_contents else file_contents
    if not contents:
        return {"findings": []}

    # Prepend unified diff and skeletons to each file so the model sees old vs new and external context
    files_with_diff: dict[str, str] = {}
    for filepath, content in contents.items():
        prefix = ""

        if call_chain_text:
            prefix += f"{call_chain_text}\n\n"

        relevant_skels = []
        for imp, skel in external_skeletons.items():
            relevant_skels.append(f"### {imp}\n{skel}")
        if relevant_skels:
            prefix += "## External Dependencies (Skeletons):\n" + "\n\n".join(relevant_skels) + "\n\n"

        # Add LSP type context if available
        if filepath in lsp_context:
            from code_review.models import LSPTypeInfo
            lsp_info = LSPTypeInfo(**lsp_context[filepath])
            lsp_str = lsp_info.to_context_str()
            if lsp_str:
                prefix += f"{lsp_str}\n\n"

        dc = diff_context.get(filepath)
        if dc and dc.get("diff"):
            prefix += f"## Changes (old → new):\n```diff\n{dc['diff']}\n```\n\n## Current code:\n"

        files_with_diff[filepath] = prefix + content

    bus.emit("agent.files", agent="logic", files=sorted(files_with_diff.keys()),
             chars={f: len(c) for f, c in files_with_diff.items()})

    all_findings = await run_per_file(
        agent_name="logic",
        agent_enum=AgentName.LOGIC,
        system_prompt=_get_system_prompt(),
        files=files_with_diff,
        category="logic",
    )

    return {"findings": all_findings}
