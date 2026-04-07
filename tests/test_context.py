"""Tests for Tier 2 context assembly."""

import os
import tempfile
from pathlib import Path

from code_review.context import _find_imports, assemble_context
from code_review.models import ToolResults


class TestImportResolver:
    def test_python_import(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass")
        imports = _find_imports("main.py", "import utils\nfrom utils import helper", str(tmp_path))
        assert "utils.py" in imports

    def test_js_relative_import(self, tmp_path):
        (tmp_path / "utils.js").write_text("export const x = 1")
        imports = _find_imports("main.js", "import { x } from './utils'", str(tmp_path))
        assert "utils.js" in imports

    def test_skips_node_modules(self, tmp_path):
        imports = _find_imports("main.js", "import React from 'react'", str(tmp_path))
        assert len(imports) == 0


class TestAssembleContext:
    def test_reads_files_once(self, tmp_path):
        # Create test files
        (tmp_path / "changed.py").write_text("print('hello')")

        tool_results = ToolResults(
            changed_files={"changed.py"},
        )

        state = assemble_context(str(tmp_path), tool_results)
        assert "changed.py" in state["file_contents"]
        assert state["file_contents"]["changed.py"] == "print('hello')"

    def test_resolves_imports(self, tmp_path):
        (tmp_path / "main.py").write_text("import helper")
        (tmp_path / "helper.py").write_text("def do_stuff(): pass")

        tool_results = ToolResults(changed_files={"main.py"})
        state = assemble_context(str(tmp_path), tool_results)

        # Both files should be in contents (main.py + its import helper.py)
        assert "main.py" in state["file_contents"]
        assert "helper.py" in state["file_contents"]
        assert "main.py" in state["import_context"]

    def test_empty_results(self, tmp_path):
        tool_results = ToolResults()
        state = assemble_context(str(tmp_path), tool_results)
        assert state["file_contents"] == {}
        assert state["findings"] == []
