"""Live Rich dashboard — shows real-time pipeline progress and log messages."""

from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock

from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text


# ── Log capture ──────────────────────────────────────────────────────────────

class _DashboardLogHandler(logging.Handler):
    """Captures WARNING+ log records into a fixed-size deque."""

    def __init__(self, buf: deque[str], lock: Lock) -> None:
        super().__init__(level=logging.WARNING)
        self._buf = buf
        self._lock = lock

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        color = "red" if record.levelno >= logging.ERROR else "yellow"
        with self._lock:
            self._buf.append(f"[{color}]{msg}[/]")


# ── Dashboard state ───────────────────────────────────────────────────────────

class ReviewDashboard:
    """Mutable state that drives the Rich Live display."""

    _PHASES = ["static_analysis", "context", "agents"]
    _PHASE_LABELS = {
        "static_analysis": "Static Analysis",
        "context":         "Context Assembly",
        "agents":          "AI Agents",
    }
    _AGENT_ORDER = ["syntax", "logic", "security", "git_history"]

    def __init__(self) -> None:
        self._lock = Lock()
        self._start = time.monotonic()

        # Phase tracking: state ∈ {pending, running, done, failed}
        self._phase_state: dict[str, str] = {p: "pending" for p in self._PHASES}
        self._phase_detail: dict[str, str] = {}  # short info string per phase

        # Agent tracking
        self._agent_state: dict[str, str] = {}   # agent → state
        self._agent_start: dict[str, float] = {} # agent → monotonic start

        # Log buffer (last 10 messages)
        self._logs: deque[str] = deque(maxlen=10)

        # Install log handler
        self._handler = _DashboardLogHandler(self._logs, self._lock)
        self._handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logging.getLogger().addHandler(self._handler)

    # ── Public API ────────────────────────────────────────────────────────────

    def phase_start(self, phase: str) -> None:
        with self._lock:
            self._phase_state[phase] = "running"

    def phase_done(self, phase: str, detail: str = "") -> None:
        with self._lock:
            self._phase_state[phase] = "done"
            if detail:
                self._phase_detail[phase] = detail

    def phase_fail(self, phase: str, detail: str = "") -> None:
        with self._lock:
            self._phase_state[phase] = "failed"
            if detail:
                self._phase_detail[phase] = detail

    def agent_start(self, agent: str) -> None:
        with self._lock:
            self._agent_state[agent] = "running"
            self._agent_start[agent] = time.monotonic()

    def agent_done(self, agent: str) -> None:
        with self._lock:
            self._agent_state[agent] = "done"

    def agent_fail(self, agent: str) -> None:
        with self._lock:
            self._agent_state[agent] = "failed"

    def set_agents(self, agents: list[str]) -> None:
        with self._lock:
            for a in agents:
                if a not in self._agent_state:
                    self._agent_state[a] = "pending"

    def remove(self) -> None:
        logging.getLogger().removeHandler(self._handler)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def render(self) -> Group:
        with self._lock:
            elapsed = time.monotonic() - self._start
            parts = [
                self._render_phases(),
                self._render_agents(),
                self._render_logs(),
                Text(f"  [dim]Elapsed: {elapsed:.0f}s[/]", end=""),
            ]
        return Group(*parts)

    def _render_phases(self) -> Panel:
        rows = []
        for phase in self._PHASES:
            state = self._phase_state[phase]
            label = self._PHASE_LABELS[phase]
            detail = self._phase_detail.get(phase, "")

            if state == "running":
                icon = Spinner("dots", style="bold blue")
                status = Text("running", style="bold blue")
            elif state == "done":
                icon = Text("✓", style="bold green")
                status = Text("done", style="green")
            elif state == "failed":
                icon = Text("✗", style="bold red")
                status = Text("failed", style="red")
            else:
                icon = Text("○", style="dim")
                status = Text("pending", style="dim")

            row = Text()
            row.append(f"  {label:<22}")
            rows.append(Columns([icon, status, Text(f"[dim]{detail}[/]", end="")], padding=(0, 1)))

        return Panel(Group(*rows), title="[bold]Pipeline[/]", border_style="blue", padding=(0, 1))

    def _render_agents(self) -> Panel:
        if not self._agent_state:
            return Panel(Text("  [dim]Waiting for agents...[/]"), title="[bold]Agents[/]", border_style="blue", padding=(0, 1))

        table = Table.grid(padding=(0, 3))
        table.add_column(width=14)
        table.add_column(width=10)
        table.add_column(width=10)

        order = [a for a in self._AGENT_ORDER if a in self._agent_state]
        order += [a for a in self._agent_state if a not in order]

        for agent in order:
            state = self._agent_state[agent]
            if state == "running":
                elapsed = time.monotonic() - self._agent_start.get(agent, time.monotonic())
                status = Text(f"running {elapsed:.0f}s", style="bold blue")
                icon = "⟳"
            elif state == "done":
                icon, status = "✓", Text("done", style="green")
            elif state == "failed":
                icon, status = "✗", Text("failed", style="red")
            elif state == "skipped":
                icon, status = "–", Text("skipped", style="dim")
            else:
                icon, status = "○", Text("pending", style="dim")

            table.add_row(f"  {icon} [bold]{agent}[/]", status)

        return Panel(table, title="[bold]Agents[/]", border_style="blue", padding=(0, 1))

    def _render_logs(self) -> Panel:
        if not self._logs:
            content = Text("  [dim]No warnings yet[/]")
        else:
            content = Group(*[Text(f"  {line}") for line in self._logs])
        return Panel(content, title="[bold]Logs[/]", border_style="dim", padding=(0, 1))
