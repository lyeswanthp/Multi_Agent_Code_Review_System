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


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json(text: str) -> list | dict:
    """Extract a JSON array or object from an LLM response.

    Handles markdown fences, leading/trailing prose, and mixed output.
    Returns a list or dict, or raises json.JSONDecodeError / ValueError.
    """
    # Try fenced block first
    match = _JSON_FENCE_RE.search(text)
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

    return json.loads(candidate)


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
        timeout = 300.0 if "localhost" in base_url or "127.0.0.1" in base_url else 60.0
        _clients[base_url] = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
    return _clients[base_url]


async def call_agent(
    agent: AgentName | str,
    messages: list[dict[str, str]],
    temperature: float = 0.1,
) -> str:
    """Call the LLM for a specific agent using its configured provider.

    On transient failure (rate limit, timeout): logs warning, returns empty string.
    On auth failure: raises immediately (config bug, not transient).
    """
    agent_name = agent.value if hasattr(agent, "value") else agent
    provider = settings.get_provider(agent_name)

    if not provider.api_key:
        logger.error("No API key configured for agent '%s' (provider: %s)", agent_name, provider.base_url)
        raise ValueError(
            f"No API key for agent '{agent_name}'. Set the appropriate env var."
        )

    client = get_client(provider.base_url, provider.api_key)

    try:
        response = await client.chat.completions.create(
            model=provider.model,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    except Exception as e:
        err_str = str(e).lower()
        if "auth" in err_str or "401" in err_str or "invalid api key" in err_str:
            logger.error("Auth failed for agent '%s' — check API key for %s", agent_name, provider.base_url)
            raise

        logger.warning(
            "Agent '%s' failed (provider: %s, model: %s): %s — returning empty result",
            agent_name, provider.base_url, provider.model, e,
        )
        return ""
