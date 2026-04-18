"""Aggressive context assembly tests — missing files, binary files, unresolvable imports,
JS resolution edge cases, large file trees, focused content integration."""

import os
from pathlib import Path

import pytest

from code_review.context import assemble_context
from code_review.models import Finding, Severity, AgentName, ToolResults





class TestAssembleContext:
    def test_missing_file_skipped_gracefully(self, tmp_path):
        """Changed file doesn't exist on disk — should not crash."""
        tool_results = ToolResults(changed_files={"ghost.py"})
        state = assemble_context(str(tmp_path), tool_results)
        assert "ghost.py" not in state["file_contents"]
        assert state["findings"] == []

    def test_binary_file_read_with_errors_replace(self, tmp_path):
        """Binary file should be read with errors='replace', not crash."""
        binary_path = tmp_path / "data.py"
        binary_path.write_bytes(b"\x00\x01\x02\xff\xfe def hello(): pass")
        tool_results = ToolResults(changed_files={"data.py"})
        state = assemble_context(str(tmp_path), tool_results)
        assert "data.py" in state["file_contents"]



    def test_empty_tool_results(self, tmp_path):
        tool_results = ToolResults()
        state = assemble_context(str(tmp_path), tool_results)
        assert state["file_contents"] == {}
        assert state["focused_contents"] == {}
        assert state["findings"] == []
        assert state["linter_findings"] == []
        assert state["semgrep_findings"] == []
        assert state["bandit_findings"] == []

    def test_focused_contents_populated_for_each_file(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
        (tmp_path / "b.py").write_text("x = 1\ny = 2\n")
        tool_results = ToolResults(changed_files={"a.py", "b.py"})
        state = assemble_context(str(tmp_path), tool_results)
        assert "a.py" in state["focused_contents"]
        assert "b.py" in state["focused_contents"]

    def test_linter_findings_serialized(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        f = Finding(severity=Severity.HIGH, file="a.py", line=1, message="unused", agent=AgentName.SYNTAX)
        tool_results = ToolResults(changed_files={"a.py"}, ruff_findings=[f])
        state = assemble_context(str(tmp_path), tool_results)
        assert len(state["linter_findings"]) == 1
        assert isinstance(state["linter_findings"][0], dict)  # serialized, not Finding

    def test_semgrep_and_bandit_serialized_separately(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        sf = Finding(severity=Severity.HIGH, file="a.py", line=1, message="sqli", agent=AgentName.SECURITY)
        bf = Finding(severity=Severity.LOW, file="a.py", line=2, message="assert", agent=AgentName.SECURITY)
        tool_results = ToolResults(changed_files={"a.py"}, semgrep_findings=[sf], bandit_findings=[bf])
        state = assemble_context(str(tmp_path), tool_results)
        assert len(state["semgrep_findings"]) == 1
        assert len(state["bandit_findings"]) == 1

    def test_many_files_all_read(self, tmp_path):
        """Stress test — 50 files all get read and focused."""
        for i in range(50):
            (tmp_path / f"file_{i}.py").write_text(f"def func_{i}():\n    return {i}\n")
        tool_results = ToolResults(changed_files={f"file_{i}.py" for i in range(50)})
        state = assemble_context(str(tmp_path), tool_results)
        assert len(state["file_contents"]) == 50
        assert len(state["focused_contents"]) == 50

    def test_changed_files_sorted(self, tmp_path):
        for name in ["c.py", "a.py", "b.py"]:
            (tmp_path / name).write_text("")
        tool_results = ToolResults(changed_files={"c.py", "a.py", "b.py"})
        state = assemble_context(str(tmp_path), tool_results)
        assert state["changed_files"] == ["a.py", "b.py", "c.py"]

    def test_graph_context_populated(self, tmp_path):
        """Knowledge graph context should be populated for Python files."""
        (tmp_path / "app.py").write_text("def hello():\n    return 'world'\n")
        tool_results = ToolResults(changed_files={"app.py"})
        state = assemble_context(str(tmp_path), tool_results)
        assert "graph_context" in state
        assert isinstance(state["graph_context"], dict)
        assert "nodes" in state["graph_context"]
        assert len(state["graph_context"]["nodes"]) > 0

    def test_graph_context_empty_for_empty_results(self, tmp_path):
        tool_results = ToolResults()
        state = assemble_context(str(tmp_path), tool_results)
        assert state["graph_context"]["nodes"] == []
        assert state["graph_context"]["edges"] == []

    def test_graph_context_contains_functions(self, tmp_path):
        (tmp_path / "logic.py").write_text("def process():\n    return compute()\ndef compute():\n    return 42\n")
        tool_results = ToolResults(changed_files={"logic.py"})
        state = assemble_context(str(tmp_path), tool_results)
        node_labels = {n.get("label") for n in state["graph_context"]["nodes"]}
        assert "process()" in node_labels
        assert "compute()" in node_labels

