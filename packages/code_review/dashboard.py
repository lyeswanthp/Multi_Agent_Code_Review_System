"""Live Rich dashboard — shows real-time pipeline progress and log messages."""

from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock

from rich.box import HEAVY
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text


# ── Colour palette ────────────────────────────────────────────────────────────
_C  = "[bold]"
_BL = f"{_C}#38bdf8]"   # blue   – running
_GR = f"{_C}#34d399]"   # green  – done
_YE = f"{_C}#fbbf24]"   # yellow – warning
_RE = f"{_C}#f87171]"   # red    – error/failed
_DI = "[dim]"            # dim    – pending/skipped
_CY = f"{_C}#22d3ee]"   # cyan   – accent
_PU = f"{_C}#c084fc]"   # purple – accent


# ── Log capture ──────────────────────────────────────────────────────────────

class _DashboardLogHandler(logging.Handler):
    """Captures WARNING+ log records into a fixed-size deque."""

    def __init__(self, buf: deque[str], lock: Lock) -> None:
        super().__init__(level=logging.WARNING)
        self._buf = buf
        self._lock = lock

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        color = _RE if record.levelno >= logging.ERROR else _YE
        with self._lock:
            self._buf.append(f"[{color[7:-1]}]{msg}[/]")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _phase_icon(state: str) -> str:
    icons = {
        "running": Spinner("dots12", text=_BL),
        "done":    f"{_GR}[/]✓",
        "failed":  f"{_RE}[/]✗",
        "pending": f"{_DI}[/]○",
    }
    return icons.get(state, f"{_DI}[/]○")


def _agent_icon(state: str) -> str:
    icons = {
        "running": f"{_BL}[/]⟳",
        "done":    f"{_GR}[/]✓",
        "failed":  f"{_RE}[/]✗",
        "skipped": f"{_DI}[/]–",
        "pending": f"{_DI}[/]○",
    }
    return icons.get(state, f"{_DI}[/]○")


def _badge(state: str) -> str:
    styles = {
        "running": "bold #38bdf8",
        "done":    "bold #34d399",
        "failed":  "bold #f87171",
        "skipped": "dim",
        "pending": "dim",
    }
    style = styles.get(state, "dim")
    return f"[{style}]{state.upper()}[/]"


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

        # Phase tracking
        self._phase_state: dict[str, str] = {p: "pending" for p in self._PHASES}
        self._phase_detail: dict[str, str] = {}

        # Agent tracking
        self._agent_state: dict[str, str] = {}
        self._agent_start: dict[str, float] = {}

        # Log buffer
        self._logs: deque[str] = deque(maxlen=12)

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
                _make_header(elapsed),
                self._render_phases(),
                self._render_agents(),
                self._render_logs(),
            ]
        return Group(*parts)

    def _render_phases(self) -> Panel:
        """Render pipeline phases as a horizontal step track."""
        rows = []
        for i, phase in enumerate(self._PHASES):
            state = self._phase_state[phase]
            label = self._PHASE_LABELS[phase]
            detail = self._phase_detail.get(phase, "")

            icon = _phase_icon(state)
            badge = _badge(state)
            detail_str = f" [dim]({detail})[/]" if detail else ""

            connector = ""
            if i < len(self._PHASES) - 1:
                conn_char = "--" if state == "done" else ".."
                connector = f" {conn_char} "

            rows.append(
                Text.from_markup(
                    f"  {icon} [bold]{label}[/]  {badge}{detail_str}{connector}",
                    style="",
                )
            )

        return Panel(
            Group(*rows),
            title="[bold #38bdf8]▸ Pipeline[/]",
            border_style="#1e3a5f",
            box=HEAVY,
            padding=(0, 1),
        )

    def _render_agents(self) -> Panel:
        """Render agents as a wrapped column of mini-cards."""
        if not self._agent_state:
            return Panel(
                Text.from_markup(f"  {_DI}Waiting for agents...[/]"),
                title="[bold #38bdf8]▸ AI Agents[/]",
                border_style="#1e3a5f",
                box=HEAVY,
                padding=(0, 1),
            )

        order = [a for a in self._AGENT_ORDER if a in self._agent_state]
        order += [a for a in self._agent_state if a not in order]

        cards: list[Text] = []
        for agent in order:
            state = self._agent_state[agent]
            icon = _agent_icon(state)
            badge = _badge(state)
            elapsed = ""
            if state == "running":
                e = time.monotonic() - self._agent_start.get(agent, time.monotonic())
                elapsed = f" {_DI}{e:.0f}s[/]"

            line = Text.from_markup(f"  {icon} {_C}{agent}[/]  {badge}{elapsed}")
            cards.append(line)

        return Panel(
            Columns(cards, padding=(0, 2), equal=True),
            title=f"[bold #38bdf8]▸ AI Agents[/] [dim]({len(order)})[/]",
            border_style="#1e3a5f",
            box=HEAVY,
            padding=(0, 1),
        )

    def _render_logs(self) -> Panel:
        """Render recent log/warning messages."""
        if not self._logs:
            content = Text.from_markup(f"  {_DI}No warnings or errors[/]")
        else:
            lines = [Text.from_markup(f"  {line}") for line in self._logs]
            content = Group(*lines)

        return Panel(
            content,
            title="[bold #38bdf8]▸ Logs[/]",
            border_style="#1e3a5f",
            box=HEAVY,
            padding=(0, 1),
        )


def _make_header(elapsed: float) -> Panel:
    """Top banner with title, elapsed time, and status."""
    if elapsed < 60:
        elapsed_str = f"{_C}#fbbf24]{elapsed:.0f}s[/]"
    else:
        elapsed_str = f"{_C}#fbbf24]{int(elapsed//60)}m {int(elapsed%60)}s[/]"

    left = Text.from_markup(
        f"  [bold #e2e8f0]Code Review[/]  [dim]Multi-Agent AI Review[/]"
    )
    right = Text.from_markup(
        f"  {_GR}[/]● [bold]LIVE[/]    {_DI}Elapsed:[/] {elapsed_str}"
    )

    table = Table(box=None, show_header=False, padding=0)
    table.add_column(justify="left")
    table.add_column(justify="right")
    table.add_row(left, right)

    return Panel(
        table,
        border_style="#1e3a5f",
        box=HEAVY,
        padding=(0, 1),
        style="on #0d1117",
    )
