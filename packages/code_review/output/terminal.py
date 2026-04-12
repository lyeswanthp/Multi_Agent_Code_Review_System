"""Terminal output adapter — Rich-based CLI output."""

from __future__ import annotations

from collections import defaultdict

from rich.console import Console
from rich.padding import Padding
from rich.rule import Rule
from rich.text import Text

from code_review.models import Finding, ReviewResult, Severity
from code_review.output.base import OutputAdapter

# (label, rich style)
SEVERITY_STYLE: dict[Severity, tuple[str, str]] = {
    Severity.CRITICAL: ("CRIT", "bold white on red"),
    Severity.HIGH:     ("HIGH", "bold red"),
    Severity.MEDIUM:   ("MED",  "bold yellow"),
    Severity.LOW:      ("LOW",  "dim"),
}

AGENT_COLOR: dict[str, str] = {
    "syntax":      "cyan",
    "logic":       "magenta",
    "security":    "bold red",
    "git_history": "blue",
    "orchestrator": "white",
}


class TerminalAdapter(OutputAdapter):
    def __init__(self) -> None:
        self.console = Console()

    def emit_progress(self, agent: str, status: str) -> None:
        pass  # Progress rendered inline in CLI

    def emit_finding(self, finding: Finding) -> None:
        pass  # All findings rendered in emit_summary

    def emit_summary(self, result: ReviewResult) -> None:
        c = self.console
        c.print()

        if not result.findings:
            c.print(Rule(style="green"))
            c.print("  [bold green]✓ No issues found.[/] Code looks clean.", justify="center")
            c.print(Rule(style="green"))
            return

        # Sort by severity desc, then group by file
        sorted_findings = sorted(result.findings, key=lambda f: -f.severity.rank)
        by_file: dict[str, list[Finding]] = defaultdict(list)
        for f in sorted_findings:
            by_file[f.file].append(f)

        c.print(Rule("[bold]Findings[/]"))

        for filepath, findings in by_file.items():
            c.print()
            c.print(f"  [bold underline]{filepath}[/]")
            c.print()
            for f in findings:
                label, sev_style = SEVERITY_STYLE.get(f.severity, ("?", ""))
                agent_color = AGENT_COLOR.get(f.agent.value, "white")
                loc = f"line {f.line}" if f.line else ""

                row = Text("    ")
                row.append(f" {label} ", style=sev_style)
                row.append("  ")
                if loc:
                    row.append(f"{loc:<10}", style="dim")
                row.append(f"[{f.agent.value}]", style=agent_color)
                row.append(f"  {f.message}")
                c.print(row)

                if f.suggestion:
                    c.print(f"               [dim]↳ {f.suggestion}[/]")

        # LLM summary
        if result.summary:
            c.print()
            c.print(Rule("[bold]Summary[/]"))
            c.print()
            c.print(Padding(result.summary, (0, 4)))

        # Stats bar
        counts: dict[str, int] = {}
        for f in result.findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1

        c.print()
        c.print(Rule())
        stats = Text("  ")
        items = list(SEVERITY_STYLE.items())
        for i, (sev, (label, style)) in enumerate(items):
            n = counts.get(sev.value, 0)
            stats.append(f"{label} {n}", style=style if n > 0 else "dim")
            if i < len(items) - 1:
                stats.append("   ·   ", style="dim")
        stats.append(f"     Total: {len(result.findings)}", style="bold")
        c.print(stats)
        c.print()
