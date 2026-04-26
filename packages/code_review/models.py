"""Pydantic models for the code review system."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {self.CRITICAL: 4, self.HIGH: 3, self.MEDIUM: 2, self.LOW: 1}[self]

    def __ge__(self, other: Severity) -> bool:
        return self.rank >= other.rank

    def __gt__(self, other: Severity) -> bool:
        return self.rank > other.rank

    def __le__(self, other: Severity) -> bool:
        return self.rank <= other.rank

    def __lt__(self, other: Severity) -> bool:
        return self.rank < other.rank


class AgentName(str, Enum):
    SYNTAX = "syntax"
    LOGIC = "logic"
    SECURITY = "security"
    GIT_HISTORY = "git_history"
    ORCHESTRATOR = "orchestrator"
    MASTER = "master"


class Finding(BaseModel):
    """A single review finding from any agent."""

    severity: Severity
    file: str
    line: int = 0
    end_line: int | None = None
    message: str
    agent: AgentName
    suggestion: str = ""
    category: str = ""

    def overlaps(self, other: Finding) -> bool:
        """Check if two findings cover overlapping line ranges in the same file."""
        if self.file != other.file:
            return False
        s_end = self.end_line or self.line
        o_end = other.end_line or other.line
        return self.line <= o_end and other.line <= s_end


class ToolResults(BaseModel):
    """Aggregated output from all Tier 1 deterministic tools."""

    ruff_findings: list[Finding] = Field(default_factory=list)
    semgrep_findings: list[Finding] = Field(default_factory=list)
    bandit_findings: list[Finding] = Field(default_factory=list)
    eslint_findings: list[Finding] = Field(default_factory=list)
    changed_files: set[str] = Field(default_factory=set)
    overlap_files: set[str] = Field(default_factory=set)
    raw_diff: str = ""

    @property
    def all_findings(self) -> list[Finding]:
        return (
            self.ruff_findings
            + self.semgrep_findings
            + self.bandit_findings
            + self.eslint_findings
        )


class ReviewResult(BaseModel):
    """Final output of a complete code review."""

    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == Severity.CRITICAL for f in self.findings)

    @property
    def exit_code(self) -> int:
        return 1 if self.findings else 0


class LSPDiagnostic(BaseModel):
    """Type error from LSP."""
    message: str
    line: int = 0
    column: int = 0
    severity: str = "error"


class LSPTypeInfo(BaseModel):
    """Type information for a single file from LSP analysis."""
    file: str = ""
    hover_types: dict[str, str] = Field(default_factory=dict)
    signatures: dict[str, str] = Field(default_factory=dict)
    symbols: list[dict] = Field(default_factory=list)
    diagnostics: list[LSPDiagnostic] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def to_context_str(self) -> str:
        """Format type info as string for LLM injection."""
        parts = []
        if self.hover_types:
            lines = [f"  {var} → {type_}" for var, type_ in self.hover_types.items()]
            parts.append("## Variable Types:\n" + "\n".join(lines))
        if self.signatures:
            lines = [f"  {fn} → {sig}" for fn, sig in self.signatures.items()]
            parts.append("## Function Signatures:\n" + "\n".join(lines))
        if self.symbols:
            lines = [f"  [{s['kind']}] {s['name']}" + (f" : {s['detail']}" if s.get('detail') else "") for s in self.symbols]
            parts.append("## Code Symbols:\n" + "\n".join(lines))
        if self.diagnostics:
            lines = [f"  Line {d.line}: {d.message}" for d in self.diagnostics]
            parts.append("## Type Errors:\n" + "\n".join(lines))
        return "\n".join(parts) if parts else ""
