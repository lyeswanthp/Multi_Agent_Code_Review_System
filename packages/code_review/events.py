"""Lightweight event bus for pipeline telemetry.

All pipeline components push structured events here.
Consumers (web dashboard, CLI dashboard) subscribe via listen().
"""

from __future__ import annotations

import json
import logging
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

logger = logging.getLogger(__name__)

EventCallback = Callable[["Event"], None]


@dataclass
class Event:
    """A single telemetry event."""

    kind: str              # e.g. "phase.start", "agent.done", "llm.request"
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({"kind": self.kind, "data": self.data, "ts": self.ts}, default=str)


class EventBus:
    """Thread-safe pub/sub event bus."""

    def __init__(self) -> None:
        self._listeners: list[EventCallback] = []
        self._lock = threading.Lock()
        self._history: list[Event] = []
        self._done = threading.Event()  # set once when review completes

    def subscribe(self, callback: EventCallback) -> None:
        with self._lock:
            self._listeners.append(callback)

    def unsubscribe(self, callback: EventCallback) -> None:
        with self._lock:
            self._listeners = [cb for cb in self._listeners if cb is not callback]

    def emit(self, kind: str, **data: Any) -> None:
        event = Event(kind=kind, data=data)
        with self._lock:
            self._history.append(event)
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(event)
            except Exception:
                logger.debug("Event listener error", exc_info=True)
        if kind == "review.done":
            self._done.set()

    @property
    def history(self) -> list[Event]:
        with self._lock:
            return list(self._history)

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._done.clear()

    def done_event(self) -> threading.Event:
        """Return the review-done event (set when review completes)."""
        return self._done


def emit_findings(findings: list[dict]) -> None:
    """Emit individual findings for real-time dashboard updates."""
    for f in findings:
        if isinstance(f, dict):
            bus.emit("finding.add", **f)
        elif hasattr(f, 'model_dump'):
            bus.emit("finding.add", **f.model_dump())


def agent_telemetry(agent_name: str):
    """Decorator that emits agent.start / agent.done / agent.fail events."""
    def decorator(fn):
        async def wrapper(state):
            from code_review.config import settings
            model = settings.get_provider(agent_name).model
            bus.emit("agent.start", agent=agent_name, model=model)
            try:
                result = await fn(state)
                n_findings = len(result.get("findings", []))
                bus.emit("agent.done", agent=agent_name, findings=n_findings)
                return result
            except Exception as e:
                bus.emit("agent.fail", agent=agent_name, error=str(e)[:200])
                raise
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


# Global singleton — import from anywhere
bus = EventBus()
