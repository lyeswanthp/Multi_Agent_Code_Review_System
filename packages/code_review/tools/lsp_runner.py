"""LSP integration for deterministic type resolution.

Uses pyright for Python and tsc for TypeScript/JavaScript.
Fetches hover types, signatures, and document symbols to provide
compiler-verified type information to agents.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import time
from pathlib import Path

from code_review.models import LSPTypeInfo, LSPDiagnostic

logger = logging.getLogger(__name__)

# Cache: {cache_key -> (timestamp, LSPTypeInfo)}
_lsp_cache: dict[str, tuple[float, LSPTypeInfo]] = {}
_CACHE_TTL_SECONDS = 3600  # 1 hour


def _get_cache_key(filepath: str, mtime: float) -> str:
    """Generate cache key based on file path and modification time."""
    return hashlib.md5(f"{filepath}:{mtime}".encode()).hexdigest()


def _is_python(filepath: str) -> bool:
    return filepath.endswith(".py")


def _is_typescript(filepath: str) -> bool:
    return filepath.endswith((".ts", ".tsx", ".js", ".jsx"))


def _check_tool_available(command: str) -> bool:
    """Check if a tool is available in PATH."""
    try:
        result = subprocess.run(
            ["which", command],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def resolve_types_pyright(filepath: str) -> LSPTypeInfo:
    """Resolve types for a Python file using pyright.

    Uses --outputjson to get diagnostics and symbol info.
    Note: True hover/signature queries require an LSP client (stdio mode).
    This captures what pyright can provide via one-shot batch analysis.
    """
    result = LSPTypeInfo(file=filepath)

    if not _check_tool_available("pyright"):
        result.errors.append("pyright not found in PATH")
        return result

    try:
        import json
        abs_path = os.path.abspath(filepath)
        proc = subprocess.run(
            ["pyright", "--outputjson", abs_path],
            capture_output=True,
            text=True,
            timeout=30
        )

        output = proc.stdout
        if not output:
            if proc.stderr:
                result.errors.append(proc.stderr[:500])
            return result

        data = json.loads(output)

        # Extract diagnostics
        if "generalDiagnostics" in data:
            for diag in data["generalDiagnostics"]:
                result.diagnostics.append(LSPDiagnostic(
                    message=diag.get("message", ""),
                    line=diag.get("range", {}).get("start", {}).get("line", 0) + 1,
                    column=diag.get("range", {}).get("start", {}).get("character", 0),
                    severity=diag.get("severity", "error")
                ))

        # Extract document symbols for function/class signatures
        if "documentSymbols" in data:
            for sym in data["documentSymbols"]:
                name = sym.get("name", "")
                kind = sym.get("kind", "")
                if name and kind in ("Class", "Function", "Method"):
                    result.symbols.append({
                        "name": name,
                        "kind": kind,
                        "detail": sym.get("detail", "")
                    })

        # Extract type errors from summary
        if "summary" in data:
            summary = data.get("summary", {})
            error_count = summary.get("errorCount", 0)
            warning_count = summary.get("warningCount", 0)
            if error_count > 0:
                result.errors.append(f"pyright: {error_count} errors, {warning_count} warnings")

    except subprocess.TimeoutExpired:
        result.errors.append("pyright timed out (>30s)")
    except Exception as e:
        result.errors.append(f"pyright failed: {e}")

    return result


def resolve_types_tsc(filepath: str) -> LSPTypeInfo:
    """Resolve types for a TypeScript/JavaScript file using tsc."""
    result = LSPTypeInfo(file=filepath)

    # Check for tsc or npx tsc
    tsc_cmd = "tsc"
    if not _check_tool_available("tsc"):
        # Try npx
        try:
            proc = subprocess.run(
                ["npx", "--yes", "typescript", "--version"],
                capture_output=True,
                text=True,
                timeout=30
            )
            if proc.returncode == 0:
                tsc_cmd = "npx tsc"
            else:
                result.errors.append("typescript not found (install via: npm install -g typescript)")
                return result
        except Exception:
            result.errors.append("typescript not found in PATH")
            return result

    try:
        # tsc --noEmit --pretty false --logFile errors.json file.ts
        # This will emit errors but not actual JS files
        if tsc_cmd == "npx tsc":
            proc = subprocess.run(
                ["npx", "-y", "tsc", "--noEmit", "--pretty", "false", filepath],
                capture_output=True,
                text=True,
                timeout=10
            )
        else:
            proc = subprocess.run(
                ["tsc", "--noEmit", "--pretty", "false", filepath],
                capture_output=True,
                text=True,
                timeout=10
            )

        # tsc outputs to stderr in format: file.ts(line,col): error message
        stderr = proc.stderr
        for line in stderr.splitlines():
            if ": error " in line:
                # Parse: "file.ts(10,5): error TS1234: message"
                parts = line.split(":", 3)
                if len(parts) >= 4:
                    try:
                        loc = parts[1].strip()
                        if loc and "," in loc:
                            line_num, col = loc.split(",")
                            result.diagnostics.append(LSPDiagnostic(
                                message=parts[3].strip() if len(parts) > 3 else line,
                                line=int(line_num),
                                column=int(col),
                                severity="error"
                            ))
                    except ValueError:
                        pass

        if proc.returncode != 0 and not result.diagnostics:
            result.errors.append(stderr[:500] if stderr else "tsc failed")

    except subprocess.TimeoutExpired:
        result.errors.append("tsc timed out (>10s)")
    except Exception as e:
        result.errors.append(f"tsc failed: {e}")

    return result


def resolve_types(filepath: str) -> LSPTypeInfo:
    """Main entry point — resolve types for a file using appropriate LSP tool."""
    abs_path = os.path.abspath(filepath)

    if not os.path.exists(abs_path):
        return LSPTypeInfo(file=filepath, errors=[f"File not found: {abs_path}"])

    # Check cache
    try:
        mtime = os.path.getmtime(abs_path)
        cache_key = _get_cache_key(abs_path, mtime)

        if cache_key in _lsp_cache:
            cached_time, cached_result = _lsp_cache[cache_key]
            if time.time() - cached_time < _CACHE_TTL_SECONDS:
                logger.debug("LSP cache hit for %s", filepath)
                return cached_result
    except Exception:
        pass

    # Resolve based on file type
    if _is_python(filepath):
        result = resolve_types_pyright(abs_path)
    elif _is_typescript(filepath):
        result = resolve_types_tsc(abs_path)
    else:
        result = LSPTypeInfo(file=filepath, errors=["Unsupported file type"])

    # Cache result
    try:
        mtime = os.path.getmtime(abs_path)
        cache_key = _get_cache_key(abs_path, mtime)
        _lsp_cache[cache_key] = (time.time(), result)
    except Exception:
        pass

    return result


def clear_cache():
    """Clear the LSP cache."""
    _lsp_cache.clear()


def get_cache_stats() -> dict:
    """Return cache statistics."""
    return {
        "entries": len(_lsp_cache),
        "ttl_seconds": _CACHE_TTL_SECONDS
    }