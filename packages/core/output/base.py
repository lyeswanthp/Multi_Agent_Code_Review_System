"""Output adapter abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from code_review.models import Finding, ReviewResult


class OutputAdapter(ABC):
    """Base class for output adapters (terminal, GitHub, SSE, etc.)."""

    @abstractmethod
    def emit_progress(self, agent: str, status: str) -> None:
        """Report agent progress (e.g., 'syntax_agent: running')."""

    @abstractmethod
    def emit_finding(self, finding: Finding) -> None:
        """Emit a single finding."""

    @abstractmethod
    def emit_summary(self, result: ReviewResult) -> None:
        """Emit the final review summary."""
