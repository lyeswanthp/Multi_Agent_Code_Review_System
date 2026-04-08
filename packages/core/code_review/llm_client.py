"""Unified OpenAI-compatible LLM client for all providers (Groq, NIM, Cerebras)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from code_review.config import settings

if TYPE_CHECKING:
    from code_review.models import AgentName

logger = logging.getLogger(__name__)

# Cache clients per base_url to reuse connections
_clients: dict[str, AsyncOpenAI] = {}


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
