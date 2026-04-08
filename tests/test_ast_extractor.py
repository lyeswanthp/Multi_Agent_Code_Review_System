"""Tests for AST-based focused context extraction."""

from code_review.ast_extractor import (
    changed_lines_from_diff,
    extract_focused_context,
    parse_blocks,
)


SAMPLE_PY = """\
import os
import sys

DB_URL = "sqlite:///test.db"

def connect():
    return os.getenv("DB_URL", DB_URL)

def process(data):
    result = []
    for item in data:
        if item is not None:
            result.append(item)
    return result

class Handler:
    def __init__(self):
        self.ready = False

    def handle(self, request):
        if not self.ready:
            raise RuntimeError("not ready")
        return request.upper()

    def cleanup(self):
        self.ready = False

def unused_helper():
    pass

def another_unused():
    x = 1
    y = 2
    z = 3
    return x + y + z

def yet_another_unused():
    data = []
    for i in range(100):
        data.append(i * 2)
    return data

def final_unused():
    config = {
        "host": "localhost",
        "port": 8080,
        "debug": True,
        "workers": 4,
    }
    return config
"""

SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -9,7 +9,7 @@ def process(data):
     result = []
     for item in data:
-        if item is not None:
+        if item is not None and item != "":
             result.append(item)
     return result
"""


class TestParseBlocks:
    def test_python_functions_and_classes(self):
        blocks = parse_blocks("app.py", SAMPLE_PY)
        assert blocks is not None
        names = {b.name for b in blocks}
        assert "connect" in names
        assert "process" in names
        assert "Handler" in names
        assert "unused_helper" in names

    def test_methods_inside_class(self):
        blocks = parse_blocks("app.py", SAMPLE_PY)
        names = {b.name for b in blocks}
        assert "__init__" in names
        assert "handle" in names
        assert "cleanup" in names

    def test_unsupported_language_returns_none(self):
        assert parse_blocks("data.csv", "a,b,c") is None
        assert parse_blocks("Makefile", "all: build") is None

    def test_javascript(self):
        js_code = "function greet(name) {\n  return 'hello ' + name;\n}\n"
        blocks = parse_blocks("app.js", js_code)
        assert blocks is not None
        assert any(b.name == "greet" for b in blocks)

    def test_typescript(self):
        ts_code = "function add(a: number, b: number): number {\n  return a + b;\n}\n"
        blocks = parse_blocks("math.ts", ts_code)
        assert blocks is not None
        assert any(b.name == "add" for b in blocks)


class TestChangedLinesFromDiff:
    def test_extracts_changed_lines(self):
        lines = changed_lines_from_diff(SAMPLE_DIFF, "app.py")
        assert len(lines) > 0
        # The + line is around line 12 in the new file
        assert any(9 <= l <= 14 for l in lines)

    def test_no_diff_returns_empty(self):
        lines = changed_lines_from_diff("", "app.py")
        assert lines == set()

    def test_different_file_returns_empty(self):
        lines = changed_lines_from_diff(SAMPLE_DIFF, "other.py")
        assert lines == set()


class TestExtractFocusedContext:
    def test_extracts_only_changed_function(self):
        focused = extract_focused_context("app.py", SAMPLE_PY, SAMPLE_DIFF)
        # Should include 'process' (the changed function)
        assert "def process" in focused
        # Should NOT include 'unused_helper' (untouched)
        assert "unused_helper" not in focused

    def test_includes_imports(self):
        focused = extract_focused_context("app.py", SAMPLE_PY, SAMPLE_DIFF)
        # Should include top-level imports
        assert "import os" in focused

    def test_shows_omission_comment(self):
        focused = extract_focused_context("app.py", SAMPLE_PY, SAMPLE_DIFF)
        assert "omitted" in focused.lower()

    def test_fallback_on_no_diff(self):
        # No diff → returns full content
        result = extract_focused_context("app.py", SAMPLE_PY, "")
        assert result == SAMPLE_PY

    def test_fallback_on_unsupported_language(self):
        result = extract_focused_context("data.rb", "puts 'hello'", SAMPLE_DIFF)
        assert result == "puts 'hello'"

    def test_reduction_is_significant(self):
        focused = extract_focused_context("app.py", SAMPLE_PY, SAMPLE_DIFF)
        # Focused should be meaningfully shorter than full
        assert len(focused) < len(SAMPLE_PY)
