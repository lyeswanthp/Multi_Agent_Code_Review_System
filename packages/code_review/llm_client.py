"""Unified OpenAI-compatible LLM client for all providers (Groq, NIM, Cerebras)."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from code_review.config import settings

if TYPE_CHECKING:
    from code_review.models import AgentName

logger = logging.getLogger(__name__)

# Cache clients per base_url to reuse connections
_clients: dict[str, AsyncOpenAI] = {}

# Read context window size from config (set LMSTUDIO_CONTEXT_SIZE in .env).
# Default 4096 for safety with small local models; cloud APIs can handle 32K+.
# Budget = context_chars * 0.55 for user msg, * 0.25 for system prompt,
# leaving ~20% for completion tokens.
def _get_budgets() -> tuple[int, int]:
    ctx_tokens = settings.lmstudio_context_size if settings.llm_mode == "local" else 32_000
    ctx_chars = ctx_tokens * 3  # ~3 chars per token (conservative)
    return int(ctx_chars * 0.55), int(ctx_chars * 0.25)  # user_budget, sys_budget


_USER_MSG_CHAR_BUDGET, _SYS_PROMPT_CHAR_BUDGET = _get_budgets()


_JSON_FENCE_CLOSED = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_FENCE_OPEN = re.compile(r"```(?:json)?\s*([\s\S]*)", re.IGNORECASE)


def extract_json(text: str) -> list | dict:
    """Extract a JSON array or object from an LLM response.

    Handles markdown fences (open or closed), leading/trailing prose, mixed output,
    and truncated arrays (salvages complete objects from cut-off responses).
    Also handles LLM thinking/reasoning tags wrapping JSON content.
    Returns a list or dict, or raises json.JSONDecodeError / ValueError.
    """
    # Strip thinking/reasoning tags that may wrap the JSON content.
    # Covers both XML-style (<think>...</think>) and Qwen internal tokens.
    text = _THINKING_RE.sub("", text)

    # Try closed fence first, then open fence (model truncated without closing ```)
    match = _JSON_FENCE_CLOSED.search(text)
    if not match:
        match = _JSON_FENCE_OPEN.search(text)
    candidate = match.group(1).strip() if match else text.strip()

    # Try to find an array
    arr_start = candidate.find("[")
    arr_end = candidate.rfind("]")

    # Try to find an object
    obj_start = candidate.find("{")
    obj_end = candidate.rfind("}")

    has_array = arr_start != -1 and arr_end != -1 and arr_end > arr_start
    has_object = obj_start != -1 and obj_end != -1 and obj_end > obj_start

    # Pick whichever structure appears first in the text
    if has_array and has_object:
        if arr_start < obj_start:
            candidate = candidate[arr_start : arr_end + 1]
        else:
            candidate = candidate[obj_start : obj_end + 1]
    elif has_array:
        candidate = candidate[arr_start : arr_end + 1]
    elif has_object:
        candidate = candidate[obj_start : obj_end + 1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Truncated array recovery: progressively close the array to salvage items
    if arr_start is not None and arr_start != -1:
        raw = candidate if candidate.startswith("[") else text[text.find("["):]
        # Try closing at each "}, {" boundary from the end
        parts = raw.split("},")
        for i in range(len(parts), 0, -1):
            attempt = "},".join(parts[:i]) + "}]"
            try:
                result = json.loads(attempt)
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                continue

    raise json.JSONDecodeError("No valid JSON found", text, 0)


def truncate_content(content: str, max_chars: int = _USER_MSG_CHAR_BUDGET) -> str:
    """Truncate a string to max_chars, appending a notice when cut."""
    if len(content) <= max_chars:
        return content
    cutoff = content[:max_chars].rfind("\n")  # break on a line boundary
    cutoff = cutoff if cutoff > max_chars // 2 else max_chars
    return content[:cutoff] + f"\n... [truncated — {len(content) - cutoff} chars omitted]"


def truncate_system_prompt(prompt: str) -> str:
    """Trim system prompt to fit within the configured context window budget."""
    return truncate_content(prompt, _SYS_PROMPT_CHAR_BUDGET)


def get_client(base_url: str, api_key: str) -> AsyncOpenAI:
    """Get or create an AsyncOpenAI client for the given provider."""
    if base_url not in _clients:
        timeout = 120.0 if "localhost" in base_url or "127.0.0.1" in base_url else 60.0
        _clients[base_url] = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
    return _clients[base_url]


_call_counter = 0

# Per-agent max_tokens caps tuned for local models (Qwen3.6 27B class)
# Higher limits for complex code review tasks.
_MAX_TOKENS: dict[str, int] = {
    "syntax": 512,
    "logic": 1024,
    "security": 512,
    "git_history": 512,
    "orchestrator": 1024,
    "prefilter": 256,
    "master": 4096,
}

_THINKING_RE = re.compile(
    r"<\|reserved_0x[0-9a-f]+\|>[\s\S]*?<\|reserved_0x[0-9a-f]+\|>"  # Qwen internal tokens
    r"|<think[\s\S]*?</think>"  # XML-style thinking tags
    r"|<thinking[\s\S]*?</thinking>",  # Lowercase thinking tags
    re.IGNORECASE,
)


async def call_agent(
    agent: AgentName | str,
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    max_tokens: int | None = None,
) -> str:
    """Call the LLM for a specific agent using its configured provider.

    On transient failure (rate limit, timeout): logs warning, returns empty string.
    On auth failure: raises immediately (config bug, not transient).
    """
    global _call_counter
    from code_review.events import bus

    agent_name = agent.value if hasattr(agent, "value") else agent
    provider = settings.get_provider(agent_name)

    if not provider.api_key:
        logger.error("No API key configured for agent '%s' (provider: %s)", agent_name, provider.base_url)
        raise ValueError(
            f"No API key for agent '{agent_name}'. Set the appropriate env var."
        )

    client = get_client(provider.base_url, provider.api_key)

    # Compute prompt size for telemetry
    prompt_chars = sum(len(m.get("content", "")) for m in messages)
    _call_counter += 1
    call_id = f"{agent_name}_{_call_counter}"

    bus.emit("llm.request",
        id=call_id, agent=agent_name, model=provider.model,
        prompt_chars=prompt_chars, base_url=provider.base_url,
        prompt="\n\n".join(m.get("content", "") for m in messages),
    )

    try:
        is_local = "localhost" in provider.base_url or "127.0.0.1" in provider.base_url

        # Disable thinking mode for Qwen models on local — they waste tokens
        # on <think> tags and return empty answers within the token budget.
        extra: dict = {}
        if is_local and "qwen" in provider.model.lower():
            extra["extra_body"] = {
                "thinking": "off",
            }

        # Temperature: balanced for code review — creativity for suggestions, consistency for patterns.
        temp = 0.2 if is_local else temperature

        create_kwargs: dict = dict(
            model=provider.model,
            messages=messages,
            temperature=temp,
            **extra,
        )
        # Use per-agent output cap (saves time on small models); honor caller's override.
        cap = _MAX_TOKENS.get(agent_name)
        if max_tokens is not None:
            cap = min(max_tokens, cap) if cap else max_tokens
        if cap:
            create_kwargs["max_tokens"] = cap

        response = await client.chat.completions.create(**create_kwargs)
        content = response.choices[0].message.content or ""

        # Strip thinking tags that slip through even with enable_thinking=False.
        # Qwen models sometimes emit them anyway when under token pressure.
        if is_local:
            content = _THINKING_RE.sub("", content).strip()

        bus.emit("llm.response",
            id=call_id, agent=agent_name,
            response_chars=len(content),
            response=content,
            prompt="\n\n".join(m.get("content", "") for m in messages),
        )
        return content

    except Exception as e:
        err_str = str(e).lower()

        bus.emit("llm.error",
            id=call_id, agent=agent_name,
            error=str(e)[:200],
        )

        if "auth" in err_str or "401" in err_str or "invalid api key" in err_str:
            logger.error("Auth failed for agent '%s' — check API key for %s", agent_name, provider.base_url)
            raise

        logger.warning(
            "Agent '%s' failed (provider: %s, model: %s): %s — returning empty result",
            agent_name, provider.base_url, provider.model, e,
        )
        return ""
