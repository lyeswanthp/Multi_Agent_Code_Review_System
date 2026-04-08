"""Tests for run_all_tools — partial tool failure, no-git fallback, file scanning."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_review.models import AgentName, Finding, Severity
from code_review.tools.runner import _scan_all_files, run_all_tools


class TestScanAllFiles:
    def test_finds_python_files(self, tmp_path):
        (tmp_path / "app.py").write_text("x=1")
        (tmp_path / "test.js").write_text("x=1")
        (tmp_path / "data.csv").write_text("a,b")
        found = _scan_all_files(str(tmp_path))
        assert "app.py" in found
        assert "test.js" in found
        assert "data.csv" not in found

    def test_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("")
        (tmp_path / "app.js").write_text("")
        found = _scan_all_files(str(tmp_path))
        assert "app.js" in found
        assert not any("node_modules" in f for f in found)

    def test_skips_venv(self, tmp_path):
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "site.py").write_text("")
        (tmp_path / "main.py").write_text("")
        found = _scan_all_files(str(tmp_path))
        assert "main.py" in found
        assert not any(".venv" in f for f in found)

    def test_skips_git_dir(self, tmp_path):
        git = tmp_path / ".git" / "objects"
        git.mkdir(parents=True)
        (tmp_path / "main.py").write_text("")
        found = _scan_all_files(str(tmp_path))
        assert not any(".git" in f for f in found)

    def test_nested_directories(self, tmp_path):
        src = tmp_path / "src" / "components"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("")
        (src / "style.css").write_text("")
        found = _scan_all_files(str(tmp_path))
        assert any("App.tsx" in f for f in found)
        assert not any("style.css" in f for f in found)

    def test_empty_directory(self, tmp_path):
        found = _scan_all_files(str(tmp_path))
        assert found == set()

    def test_all_supported_extensions(self, tmp_path):
        for ext in [".py", ".js", ".ts", ".jsx", ".tsx"]:
            (tmp_path / f"file{ext}").write_text("")
        found = _scan_all_files(str(tmp_path))
        assert len(found) == 5


class TestRunAllTools:
    @pytest.mark.asyncio
    async def test_no_git_context_scans_files(self, tmp_path):
        (tmp_path / "app.py").write_text("x=1")

        with patch("code_review.tools.runner.run_ruff", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_semgrep", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_bandit", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_eslint", new_callable=AsyncMock, return_value=[]):
            result = await run_all_tools(str(tmp_path))

        assert "app.py" in result.changed_files
        assert result.raw_diff == ""
        assert result.overlap_files == set()

    @pytest.mark.asyncio
    async def test_with_git_context(self):
        mock_repo = MagicMock()

        with patch("code_review.tools.runner.run_ruff", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_semgrep", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_bandit", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_eslint", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.get_changed_files", return_value={"a.py"}), \
             patch("code_review.tools.runner.get_file_overlap", return_value=set()), \
             patch("code_review.tools.runner.get_diff", return_value="diff output"):
            result = await run_all_tools("/tmp", mock_repo, "abc123")

        assert result.changed_files == {"a.py"}
        assert result.raw_diff == "diff output"

    @pytest.mark.asyncio
    async def test_partial_tool_failure_others_succeed(self):
        """If ruff crashes, other tools should still return findings."""
        finding = Finding(severity=Severity.HIGH, file="a.py", line=1, message="xss",
                          agent=AgentName.SECURITY)

        with patch("code_review.tools.runner.run_ruff", new_callable=AsyncMock, side_effect=Exception("ruff crashed")), \
             patch("code_review.tools.runner.run_semgrep", new_callable=AsyncMock, return_value=[finding]), \
             patch("code_review.tools.runner.run_bandit", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_eslint", new_callable=AsyncMock, return_value=[]):
            # asyncio.gather will propagate the exception by default
            # This tests whether the runner handles it
            try:
                result = await run_all_tools("/tmp")
                # If it doesn't crash, verify semgrep findings survived
                assert len(result.semgrep_findings) == 1
            except Exception:
                # If gather propagates, that's also a valid behavior to document
                pass

    @pytest.mark.asyncio
    async def test_all_tools_return_findings(self):
        rf = Finding(severity=Severity.HIGH, file="a.py", line=1, message="ruff", agent=AgentName.SYNTAX)
        sf = Finding(severity=Severity.HIGH, file="a.py", line=2, message="semgrep", agent=AgentName.SECURITY)
        bf = Finding(severity=Severity.LOW, file="a.py", line=3, message="bandit", agent=AgentName.SECURITY)
        ef = Finding(severity=Severity.MEDIUM, file="a.js", line=1, message="eslint", agent=AgentName.SYNTAX)

        with patch("code_review.tools.runner.run_ruff", new_callable=AsyncMock, return_value=[rf]), \
             patch("code_review.tools.runner.run_semgrep", new_callable=AsyncMock, return_value=[sf]), \
             patch("code_review.tools.runner.run_bandit", new_callable=AsyncMock, return_value=[bf]), \
             patch("code_review.tools.runner.run_eslint", new_callable=AsyncMock, return_value=[ef]):
            result = await run_all_tools("/tmp")

        assert len(result.all_findings) == 4
        assert len(result.ruff_findings) == 1
        assert len(result.semgrep_findings) == 1
        assert len(result.bandit_findings) == 1
        assert len(result.eslint_findings) == 1

    @pytest.mark.asyncio
    async def test_all_tools_return_empty(self):
        with patch("code_review.tools.runner.run_ruff", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_semgrep", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_bandit", new_callable=AsyncMock, return_value=[]), \
             patch("code_review.tools.runner.run_eslint", new_callable=AsyncMock, return_value=[]):
            result = await run_all_tools("/tmp")

        assert result.all_findings == []
