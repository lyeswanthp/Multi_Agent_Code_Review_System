"""Aggressive context assembly tests — missing files, binary files, unresolvable imports,
JS resolution edge cases, large file trees, focused content integration."""

import os
from pathlib import Path

import pytest

from code_review.context import (
    _find_imports,
    _resolve_js_import,
    _resolve_py_import,
    assemble_context,
)
from code_review.models import Finding, Severity, AgentName, ToolResults


class TestPythonImportResolution:
    def test_dotted_module(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "sub.py").write_text("x = 1")
        result = _resolve_py_import("pkg.sub", str(tmp_path))
        assert result == "pkg/sub.py"

    def test_package_init(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("")
        result = _resolve_py_import("pkg", str(tmp_path))
        assert result == "pkg/__init__.py"

    def test_nonexistent_module(self, tmp_path):
        result = _resolve_py_import("nonexistent", str(tmp_path))
        assert result is None

    def test_stdlib_not_resolved(self, tmp_path):
        """stdlib modules like 'os' have no file in repo — should return None."""
        result = _resolve_py_import("os", str(tmp_path))
        assert result is None


class TestJSImportResolution:
    def test_relative_import_with_extension(self, tmp_path):
        (tmp_path / "utils.js").write_text("export const x = 1")
        result = _resolve_js_import("./utils", "main.js", str(tmp_path))
        assert result == "utils.js"

    def test_relative_import_ts(self, tmp_path):
        (tmp_path / "utils.ts").write_text("export const x = 1")
        result = _resolve_js_import("./utils", "main.ts", str(tmp_path))
        assert result == "utils.ts"

    def test_index_file(self, tmp_path):
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "index.js").write_text("")
        result = _resolve_js_import("./lib", "main.js", str(tmp_path))
        assert result == "lib/index.js"

    def test_node_modules_import_ignored(self, tmp_path):
        result = _resolve_js_import("react", "main.js", str(tmp_path))
        assert result is None

    def test_scoped_npm_package_ignored(self, tmp_path):
        result = _resolve_js_import("@tanstack/react-query", "main.tsx", str(tmp_path))
        assert result is None

    def test_parent_directory_import(self, tmp_path):
        (tmp_path / "shared").mkdir()
        (tmp_path / "shared" / "utils.js").write_text("")
        (tmp_path / "src").mkdir()
        result = _resolve_js_import("../shared/utils", "src/main.js", str(tmp_path))
        assert result == "shared/utils.js"

    def test_nonexistent_relative_import(self, tmp_path):
        result = _resolve_js_import("./missing", "main.js", str(tmp_path))
        assert result is None


class TestFindImports:
    def test_python_from_import(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass")
        content = "from utils import helper\nfrom os import path"
        imports = _find_imports("main.py", content, str(tmp_path))
        assert "utils.py" in imports
        # os should not be resolved
        assert not any("os" in i for i in imports)

    def test_python_multiple_imports(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        content = "import a\nimport b\nimport nonexistent"
        imports = _find_imports("main.py", content, str(tmp_path))
        assert "a.py" in imports
        assert "b.py" in imports
        assert len(imports) == 2  # nonexistent excluded

    def test_js_mixed_imports(self, tmp_path):
        (tmp_path / "utils.js").write_text("")
        content = "import { x } from './utils'\nimport React from 'react'\nconst y = require('./utils')"
        imports = _find_imports("main.js", content, str(tmp_path))
        # Both ES import and require resolve to same file
        assert "utils.js" in imports

    def test_unsupported_file_type(self, tmp_path):
        imports = _find_imports("data.csv", "import something", str(tmp_path))
        assert imports == []

    def test_empty_content(self, tmp_path):
        imports = _find_imports("main.py", "", str(tmp_path))
        assert imports == []


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

    def test_import_chain_reads_imported_files(self, tmp_path):
        (tmp_path / "main.py").write_text("import helper")
        (tmp_path / "helper.py").write_text("def do_stuff(): pass")
        tool_results = ToolResults(changed_files={"main.py"})
        state = assemble_context(str(tmp_path), tool_results)
        assert "main.py" in state["file_contents"]
        assert "helper.py" in state["file_contents"]
        assert "main.py" in state["import_context"]
        assert "helper.py" in state["import_context"]["main.py"]

    def test_duplicate_imports_read_once(self, tmp_path):
        """If two changed files import the same module, it should appear once."""
        (tmp_path / "a.py").write_text("import shared")
        (tmp_path / "b.py").write_text("import shared")
        (tmp_path / "shared.py").write_text("x = 1")
        tool_results = ToolResults(changed_files={"a.py", "b.py"})
        state = assemble_context(str(tmp_path), tool_results)
        assert "shared.py" in state["file_contents"]

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

