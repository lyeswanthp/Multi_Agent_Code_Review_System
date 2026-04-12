"""Simple hash-based cache for agent results.

Avoids re-running agents on identical input. Cache is in-memory per process,
suitable for repeated reviews during development.
"""

from __future__ import annotations

import hashlib
import json
import logging

logger = logging.getLogger(__name__)

_cache: dict[str, list[dict]] = {}


def _make_key(agent_name: str, content: str) -> str:
    """Create a cache key from agent name + content hash."""
    h = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{agent_name}:{h}"


def get_cached(agent_name: str, content: str) -> list[dict] | None:
    """Return cached findings for this agent+content, or None if miss."""
    key = _make_key(agent_name, content)
    if key in _cache:
        logger.info("Cache HIT for %s", agent_name)
        return _cache[key]
    return None


def set_cached(agent_name: str, content: str, findings: list[dict]) -> None:
    """Store agent findings in cache."""
    key = _make_key(agent_name, content)
    _cache[key] = findings
    logger.debug("Cached %d findings for %s", len(findings), agent_name)


def clear_cache() -> None:
    """Clear all cached results."""
    _cache.clear()
