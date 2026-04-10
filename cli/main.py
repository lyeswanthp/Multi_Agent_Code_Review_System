"""CLI entry point — Typer app that wires Tier 1 -> Tier 2 -> Tier 3 -> Output."""

from __future__ import annotations

import asyncio
import json
import logging

import typer
from rich.console import Console
from rich.live import Live

from code_review.context import assemble_context
from code_review.dashboard import ReviewDashboard
from code_review.events import bus
from code_review.graph import review_graph
from code_review.models import ReviewResult, Severity
from code_review.noise_filter import apply_noise_filter
from code_review.output.github import GitHubAdapter
from code_review.output.terminal import TerminalAdapter
from code_review.tools.runner import run_all_tools
from code_review.web_dashboard import DashboardServer

app = typer.Typer(name="code-review", help="Multi-agent code review system")
console = Console()


async def _run_review(
    path: str,
    repo_path: str | None = None,
    commit_sha: str | None = None,
    severity: Severity = Severity.MEDIUM,
) -> ReviewResult:
    """Execute the full review pipeline."""
    from git import Repo, InvalidGitRepositoryError

    repo = None
    if repo_path:
        repo = Repo(repo_path)
    else:
        # Auto-detect git repo from path
        try:
            repo = Repo(path, search_parent_directories=True)
            bus.emit("log.warning", logger="cli",
                     message=f"Auto-detected git repo: {repo.working_dir}")
        except (InvalidGitRepositoryError, Exception):
            pass  # not a git repo — will fall back to full scan

    bus.clear()
    bus.emit("review.start", path=path)

    # -- Tier 1: Static analysis ------------------------------------------------
    bus.emit("phase.start", phase="static_analysis")
    tool_results = await run_all_tools(path, repo, commit_sha)
    t1_count = len(tool_results.all_findings)
    tools_run = [
        t for t, findings in [
            ("ruff",    tool_results.ruff_findings),
            ("semgrep", tool_results.semgrep_findings),
            ("bandit",  tool_results.bandit_findings),
            ("eslint",  tool_results.eslint_findings),
        ] if findings
    ]
    bus.emit("phase.done", phase="static_analysis",
             detail=f"{', '.join(tools_run) or 'none'}  ·  {t1_count} findings")

    # -- Tier 2: Context assembly ------------------------------------------------
    bus.emit("phase.start", phase="context")
    state = assemble_context(path, tool_results, repo, commit_sha)
    bus.emit("phase.done", phase="context", detail=f"{len(state['file_contents'])} files")

    # -- Tier 3: AI agents -------------------------------------------------------
    bus.emit("phase.start", phase="agents")

    result_state: dict = {}
    async for chunk in review_graph.astream(state):
        for node_name, output in chunk.items():
            if isinstance(output, dict):
                for k, v in output.items():
                    if k == "findings" and isinstance(v, list):
                        existing = result_state.get("findings", [])
                        result_state["findings"] = existing + v
                    else:
                        result_state[k] = v

    agents_ran = result_state.get("agents_to_run", [])
    bus.emit("phase.done", phase="agents", detail=", ".join(agents_ran) if agents_ran else "none")

    # -- Post-processing ---------------------------------------------------------
    findings = result_state.get("findings", [])
    findings = apply_noise_filter(findings, severity)

    bus.emit("review.done",
             total_findings=len(findings),
             files_reviewed=len(state["file_contents"]))

    return ReviewResult(
        findings=findings,
        summary=result_state.get("summary", ""),
        metadata={
            "files_reviewed": len(state["file_contents"]),
            "tool_findings": t1_count,
            "agent_findings_raw": len(result_state.get("findings", [])),
            "agent_findings_filtered": len(findings),
        },
    )


@app.command()
def review(
    path: str = typer.Argument(".", help="Path to review"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="Git repo path"),
    sha: str | None = typer.Option(None, "--sha", "-s", help="Commit SHA to review"),
    severity: str = typer.Option("medium", "--severity", "-S",
                                 help="Minimum severity: low, medium, high, critical"),
    format: str = typer.Option("terminal", "--format", "-f",
                               help="Output format: terminal, json, github"),
    gh_owner: str | None = typer.Option(None, "--gh-owner", help="GitHub repo owner"),
    gh_repo: str | None = typer.Option(None, "--gh-repo", help="GitHub repo name"),
    gh_pr: int | None = typer.Option(None, "--gh-pr", help="GitHub PR number"),
    dashboard_port: int = typer.Option(9120, "--dashboard-port", "-d",
                                       help="Web dashboard port (0 to disable)"),
) -> None:
    """Run a multi-agent code review on the given path."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    # Capture WARNING+ logs into the event bus for the web dashboard
    _install_log_bridge()

    sev = Severity(severity.lower())

    # Start web dashboard server
    web_server: DashboardServer | None = None
    if dashboard_port > 0:
        web_server = DashboardServer(port=dashboard_port)
        url = web_server.start()
        console.print(f"\n[bold blue]Dashboard:[/] [link={url}]{url}[/link]\n")

    # Rich terminal live dashboard
    dash = ReviewDashboard()

    # Wire event bus -> terminal dashboard
    def _on_event(event):
        d = event.data
        kind = event.kind
        if kind == "phase.start":
            dash.phase_start(d.get("phase", ""))
        elif kind == "phase.done":
            dash.phase_done(d.get("phase", ""), d.get("detail", ""))
        elif kind == "phase.fail":
            dash.phase_fail(d.get("phase", ""), d.get("detail", ""))
        elif kind == "agent.set":
            dash.set_agents(d.get("agents", []))
        elif kind == "agent.start":
            dash.agent_start(d.get("agent", ""))
        elif kind == "agent.done":
            dash.agent_done(d.get("agent", ""))
        elif kind == "agent.fail":
            dash.agent_fail(d.get("agent", ""))

    bus.subscribe(_on_event)

    result: ReviewResult | None = None

    with Live(dash.render(), refresh_per_second=4, console=console,
              vertical_overflow="visible") as live:

        async def _run_and_refresh() -> ReviewResult:
            task = asyncio.create_task(_run_review(path, repo, sha, sev))
            while not task.done():
                live.update(dash.render())
                await asyncio.sleep(0.25)
            live.update(dash.render())
            return await task

        result = asyncio.run(_run_and_refresh())

    bus.unsubscribe(_on_event)
    dash.remove()

    # Output results
    if format == "json":
        print(json.dumps(result.model_dump(), indent=2, default=str))
    elif format == "github":
        if not (gh_owner and gh_repo and gh_pr):
            console.print("[red]GitHub format requires --gh-owner, --gh-repo, and --gh-pr[/]")
            raise typer.Exit(code=2)
        adapter = GitHubAdapter(gh_owner, gh_repo, gh_pr)
        for finding in result.findings:
            adapter.emit_finding(finding)
        adapter.emit_summary(result)
        print(adapter.to_json())
    else:
        adapter = TerminalAdapter()
        adapter.emit_summary(result)

    if web_server:
        web_server.stop()

    raise typer.Exit(code=result.exit_code)


def _install_log_bridge():
    """Bridge WARNING+ log records to the event bus."""
    class _LogBridge(logging.Handler):
        def emit(self, record: logging.LogRecord):
            level = "error" if record.levelno >= logging.ERROR else "warning"
            bus.emit(f"log.{level}",
                     logger=record.name,
                     message=self.format(record))

    handler = _LogBridge()
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)


if __name__ == "__main__":
    app()
