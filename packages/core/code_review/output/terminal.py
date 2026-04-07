"""Terminal output adapter — Rich-based CLI output."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from code_review.models import Finding, ReviewResult, Severity
from code_review.output.base import OutputAdapter

SEVERITY_COLORS = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "dim",
}


class TerminalAdapter(OutputAdapter):
    def __init__(self) -> None:
        self.console = Console()

    def emit_progress(self, agent: str, status: str) -> None:
        icon = {"running": "[bold blue]...", "done": "[green]OK", "skipped": "[dim]--", "failed": "[red]!!"}
        self.console.print(f"  {icon.get(status, '??')}[/] {agent}: {status}")

    def emit_finding(self, finding: Finding) -> None:
        color = SEVERITY_COLORS.get(finding.severity, "")
        location = f"{finding.file}:{finding.line}" if finding.line else finding.file
        self.console.print(f"  [{color}]{finding.severity.value.upper()}[/] {location}")
        self.console.print(f"    {finding.message}")
        if finding.suggestion:
            self.console.print(f"    [dim]Fix: {finding.suggestion}[/]")

    def emit_summary(self, result: ReviewResult) -> None:
        self.console.print()

        if not result.findings:
            self.console.print(Panel("[green]No issues found. Code looks clean.[/]", title="Review Complete"))
            return

        # Summary panel
        if result.summary:
            self.console.print(Panel(result.summary, title="Review Summary"))

        # Findings table grouped by file
        table = Table(title="Findings", show_lines=True)
        table.add_column("Sev", width=8, justify="center")
        table.add_column("Location", width=30)
        table.add_column("Message", ratio=2)
        table.add_column("Agent", width=12)

        for f in result.findings:
            color = SEVERITY_COLORS.get(f.severity, "")
            location = f"{f.file}:{f.line}" if f.line else f.file
            table.add_row(
                Text(f.severity.value.upper(), style=color),
                location,
                f.message,
                f.agent.value,
            )

        self.console.print(table)

        # Stats
        counts = {}
        for f in result.findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
        stats = " | ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        self.console.print(f"\n[bold]Total: {len(result.findings)} findings[/] ({stats})")
