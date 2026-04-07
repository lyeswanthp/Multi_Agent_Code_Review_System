"""CLI entry point — Typer app that wires Tier 1 → Tier 2 → Tier 3 → Output."""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import typer
from rich.console import Console

from code_review.config import settings
from code_review.context import assemble_context
from code_review.graph import review_graph
from code_review.models import ReviewResult, Severity
from code_review.noise_filter import apply_noise_filter
from code_review.output.github import GitHubAdapter
from code_review.output.terminal import TerminalAdapter
from code_review.tools.runner import run_all_tools

app = typer.Typer(name="code-review", help="Multi-agent code review system")
console = Console()


async def _run_review(
    path: str,
    repo_path: str | None = None,
    commit_sha: str | None = None,
    severity: Severity = Severity.MEDIUM,
) -> ReviewResult:
    """Execute the full review pipeline."""
    from git import Repo

    repo = None
    if repo_path:
        repo = Repo(repo_path)

    # Tier 1: Run deterministic tools
    console.print("[bold blue]Tier 1:[/] Running linters and SAST tools...")
    tool_results = await run_all_tools(path, repo, commit_sha)
    console.print(f"  Found {len(tool_results.all_findings)} raw findings from tools")

    # Tier 2: Assemble context
    console.print("[bold blue]Tier 2:[/] Assembling context...")
    state = assemble_context(path, tool_results, repo, commit_sha)
    console.print(f"  {len(state['file_contents'])} files loaded, {len(state['overlap_files'])} overlap files")

    # Tier 3: Run agent graph
    console.print("[bold blue]Tier 3:[/] Running AI agents in parallel...")
    result_state = await review_graph.ainvoke(state)

    # Post-processing: noise filter
    findings = result_state.get("findings", [])
    findings = apply_noise_filter(findings, severity)

    return ReviewResult(
        findings=findings,
        summary=result_state.get("summary", ""),
        metadata={
            "files_reviewed": len(state["file_contents"]),
            "tool_findings": len(tool_results.all_findings),
            "agent_findings_raw": len(result_state.get("findings", [])),
            "agent_findings_filtered": len(findings),
        },
    )


@app.command()
def review(
    path: str = typer.Argument(".", help="Path to review"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="Git repo path"),
    sha: str | None = typer.Option(None, "--sha", "-s", help="Commit SHA to review"),
    severity: str = typer.Option("medium", "--severity", "-S", help="Minimum severity: low, medium, high, critical"),
    format: str = typer.Option("terminal", "--format", "-f", help="Output format: terminal, json, github"),
    gh_owner: str | None = typer.Option(None, "--gh-owner", help="GitHub repo owner (for github format)"),
    gh_repo: str | None = typer.Option(None, "--gh-repo", help="GitHub repo name (for github format)"),
    gh_pr: int | None = typer.Option(None, "--gh-pr", help="GitHub PR number (for github format)"),
) -> None:
    """Run a multi-agent code review on the given path."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    sev = Severity(severity.lower())
    result = asyncio.run(_run_review(path, repo, sha, sev))

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

    raise typer.Exit(code=result.exit_code)


if __name__ == "__main__":
    app()
