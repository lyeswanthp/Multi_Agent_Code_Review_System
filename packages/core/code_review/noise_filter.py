"""Deterministic noise filter — dedup, merge overlapping ranges, severity threshold."""

from __future__ import annotations

from code_review.config import settings
from code_review.models import Finding, Severity


def _dedup_key(f: Finding) -> str:
    """Generate a dedup key from file + line range + category."""
    return f"{f.file}:{f.line}-{f.end_line or f.line}:{f.category}"


def deduplicate(findings: list[Finding]) -> list[Finding]:
    """Remove duplicate findings, keeping the highest severity version."""
    seen: dict[str, Finding] = {}
    for f in findings:
        key = _dedup_key(f)
        if key not in seen or f.severity > seen[key].severity:
            seen[key] = f
    return list(seen.values())


def merge_overlapping(findings: list[Finding]) -> list[Finding]:
    """Merge findings that cover overlapping line ranges in the same file."""
    if len(findings) <= 1:
        return findings

    # Group by file + category
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        key = f"{f.file}:{f.category}"
        groups.setdefault(key, []).append(f)

    merged: list[Finding] = []
    for group in groups.values():
        group.sort(key=lambda f: f.line)
        current = group[0]
        for next_f in group[1:]:
            if current.overlaps(next_f):
                # Merge: extend range, keep higher severity, combine messages
                current = Finding(
                    severity=max(current.severity, next_f.severity, key=lambda s: s.rank),
                    file=current.file,
                    line=min(current.line, next_f.line),
                    end_line=max(current.end_line or current.line, next_f.end_line or next_f.line),
                    message=f"{current.message}; {next_f.message}",
                    agent=current.agent,
                    suggestion=current.suggestion or next_f.suggestion,
                    category=current.category,
                )
            else:
                merged.append(current)
                current = next_f
        merged.append(current)

    return merged


def filter_by_severity(
    findings: list[Finding],
    threshold: Severity | None = None,
) -> list[Finding]:
    """Remove findings below the severity threshold."""
    min_severity = threshold or settings.severity_threshold
    return [f for f in findings if f.severity >= min_severity]


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Sort by severity (desc) → file → line."""
    return sorted(findings, key=lambda f: (-f.severity.rank, f.file, f.line))


def apply_noise_filter(
    findings: list[Finding],
    threshold: Severity | None = None,
) -> list[Finding]:
    """Full noise filter pipeline: dedup → merge → threshold → sort."""
    findings = deduplicate(findings)
    findings = merge_overlapping(findings)
    findings = filter_by_severity(findings, threshold)
    findings = sort_findings(findings)
    return findings
