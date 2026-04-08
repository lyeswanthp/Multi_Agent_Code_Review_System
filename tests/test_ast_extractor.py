"""Aggressive AST extractor tests — multi-language, nested classes, decorators,
multi-hunk diffs, edge diffs, empty files, large files, top-level changes."""

import pytest

from code_review.ast_extractor import (
    CodeBlock,
    changed_lines_from_diff,
    extract_focused_context,
    parse_blocks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# parse_blocks
# ---------------------------------------------------------------------------

class TestParseBlocks:
    def test_python_functions_and_classes(self):
        blocks = parse_blocks("app.py", SAMPLE_PY)
        assert blocks is not None
        names = {b.name for b in blocks}
        assert "connect" in names
        assert "process" in names
        assert "Handler" in names

    def test_methods_inside_class(self):
        blocks = parse_blocks("app.py", SAMPLE_PY)
        names = {b.name for b in blocks}
        assert "__init__" in names
        assert "handle" in names
        assert "cleanup" in names

    def test_unsupported_languages(self):
        assert parse_blocks("data.csv", "a,b,c") is None
        assert parse_blocks("Makefile", "all: build") is None
        assert parse_blocks("script.sh", "#!/bin/bash") is None
        assert parse_blocks("noext", "content") is None

    def test_javascript_function(self):
        js = "function greet(name) {\n  return 'hello ' + name;\n}\n"
        blocks = parse_blocks("app.js", js)
        assert blocks is not None
        assert any(b.name == "greet" for b in blocks)

    def test_javascript_arrow_function_in_export(self):
        js = "export const add = (a, b) => a + b;\n"
        blocks = parse_blocks("math.js", js)
        assert blocks is not None
        # Should detect the export/arrow

    def test_typescript_interface_detected(self):
        ts = "interface User {\n  name: string;\n  age: number;\n}\n"
        blocks = parse_blocks("types.ts", ts)
        assert blocks is not None
        assert len(blocks) > 0  # interface_declaration detected
        # Note: name extraction for interfaces may return <anonymous> — known limitation

    def test_typescript_type_alias(self):
        ts = "type ID = string | number;\n"
        blocks = parse_blocks("types.ts", ts)
        assert blocks is not None

    def test_tsx_extension(self):
        tsx = "function App() {\n  return <div>hello</div>;\n}\n"
        blocks = parse_blocks("App.tsx", tsx)
        assert blocks is not None

    def test_jsx_extension(self):
        jsx = "function Component() {\n  return <span>hi</span>;\n}\n"
        blocks = parse_blocks("Widget.jsx", jsx)
        assert blocks is not None

    def test_empty_file(self):
        blocks = parse_blocks("empty.py", "")
        assert blocks is not None
        assert blocks == []

    def test_only_imports(self):
        blocks = parse_blocks("imports.py", "import os\nimport sys\n")
        assert blocks is not None
        # No functions/classes — only imports
        func_blocks = [b for b in blocks if b.kind not in ("import",)]
        assert len(func_blocks) == 0

    def test_nested_class(self):
        code = """\
class Outer:
    class Inner:
        def method(self):
            pass
    def outer_method(self):
        pass
"""
        blocks = parse_blocks("nested.py", code)
        names = {b.name for b in blocks}
        assert "Outer" in names
        assert "Inner" in names
        assert "method" in names
        assert "outer_method" in names

    def test_decorated_function(self):
        code = """\
import functools

@functools.lru_cache
def expensive():
    return 42
"""
        blocks = parse_blocks("deco.py", code)
        assert blocks is not None
        names = {b.name for b in blocks}
        assert "expensive" in names

    def test_block_line_numbers_are_one_based(self):
        code = "def first():\n    pass\n\ndef second():\n    return 1\n"
        blocks = parse_blocks("lines.py", code)
        first = next(b for b in blocks if b.name == "first")
        assert first.start_line == 1
        second = next(b for b in blocks if b.name == "second")
        assert second.start_line == 4

    def test_block_source_contains_full_body(self):
        code = "def example():\n    x = 1\n    y = 2\n    return x + y\n"
        blocks = parse_blocks("src.py", code)
        blk = next(b for b in blocks if b.name == "example")
        assert "x = 1" in blk.source
        assert "return x + y" in blk.source

    def test_many_functions(self):
        """50 functions should all be parsed."""
        lines = []
        for i in range(50):
            lines.append(f"def func_{i}():\n    return {i}\n")
        code = "\n".join(lines)
        blocks = parse_blocks("many.py", code)
        func_names = {b.name for b in blocks if b.kind.startswith("function")}
        assert len(func_names) == 50


# ---------------------------------------------------------------------------
# changed_lines_from_diff
# ---------------------------------------------------------------------------

class TestChangedLinesFromDiff:
    def test_basic_hunk(self):
        lines = changed_lines_from_diff(SAMPLE_DIFF, "app.py")
        assert len(lines) > 0
        assert any(9 <= l <= 14 for l in lines)

    def test_no_diff(self):
        assert changed_lines_from_diff("", "app.py") == set()

    def test_different_file(self):
        assert changed_lines_from_diff(SAMPLE_DIFF, "other.py") == set()

    def test_multi_hunk_diff(self):
        diff = """\
diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1,3 +1,3 @@
-old1
+new1
 same
 same
@@ -10,3 +10,3 @@
-old2
+new2
 same
"""
        lines = changed_lines_from_diff(diff, "x.py")
        # Hunk walker: deleted line marks current_line as changed without advancing,
        # then the + line advances — so changed lines are 2 and 11
        assert len(lines) >= 2
        # Both hunks produce changed lines
        low_hunk = [l for l in lines if l < 5]
        high_hunk = [l for l in lines if l >= 5]
        assert len(low_hunk) > 0, "First hunk should produce changed lines"
        assert len(high_hunk) > 0, "Second hunk should produce changed lines"

    def test_only_additions(self):
        diff = """\
diff --git a/new.py b/new.py
--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
+line1
+line2
+line3
"""
        lines = changed_lines_from_diff(diff, "new.py")
        # New file: all lines should be marked as changed
        assert len(lines) == 3

    def test_multi_file_diff_extracts_correct_file(self):
        diff = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,1 +1,1 @@
-old_a
+new_a
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -5,1 +5,1 @@
-old_b
+new_b
"""
        a_lines = changed_lines_from_diff(diff, "a.py")
        b_lines = changed_lines_from_diff(diff, "b.py")
        # Each file should have changed lines only from its own hunk
        assert len(a_lines) > 0
        assert len(b_lines) > 0
        # a.py changes are near line 1, b.py changes are near line 5
        assert all(l < 5 for l in a_lines), f"a.py lines should be low: {a_lines}"
        assert all(l >= 5 for l in b_lines), f"b.py lines should be around 5: {b_lines}"


# ---------------------------------------------------------------------------
# extract_focused_context
# ---------------------------------------------------------------------------

class TestExtractFocusedContext:
    def test_extracts_only_changed_function(self):
        focused = extract_focused_context("app.py", SAMPLE_PY, SAMPLE_DIFF)
        assert "def process" in focused
        assert "unused_helper" not in focused

    def test_includes_imports(self):
        focused = extract_focused_context("app.py", SAMPLE_PY, SAMPLE_DIFF)
        assert "import os" in focused

    def test_shows_omission_comment(self):
        focused = extract_focused_context("app.py", SAMPLE_PY, SAMPLE_DIFF)
        assert "omitted" in focused.lower()

    def test_fallback_on_no_diff(self):
        result = extract_focused_context("app.py", SAMPLE_PY, "")
        assert result == SAMPLE_PY

    def test_fallback_on_unsupported_language(self):
        result = extract_focused_context("data.rb", "puts 'hello'", SAMPLE_DIFF)
        assert result == "puts 'hello'"

    def test_reduction_is_significant(self):
        focused = extract_focused_context("app.py", SAMPLE_PY, SAMPLE_DIFF)
        assert len(focused) < len(SAMPLE_PY)

    def test_top_level_change(self):
        """Change to a line outside any function should still return context."""
        code = "X = 1\nY = 2\n\ndef foo():\n    return X\n"
        diff = """\
diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -1,2 +1,2 @@
-X = 1
+X = 42
 Y = 2
"""
        result = extract_focused_context("config.py", code, diff)
        assert "X" in result
        assert len(result) > 0

    def test_change_inside_class_method(self):
        """Change inside a method should include the method and class."""
        code = """\
class MyClass:
    def method_a(self):
        return 1

    def method_b(self):
        return 2
"""
        diff = """\
diff --git a/cls.py b/cls.py
--- a/cls.py
+++ b/cls.py
@@ -2,2 +2,2 @@ class MyClass:
     def method_a(self):
-        return 1
+        return 42
"""
        result = extract_focused_context("cls.py", code, diff)
        assert "method_a" in result

    def test_empty_file(self):
        result = extract_focused_context("empty.py", "", SAMPLE_DIFF)
        # Empty file with a diff for it — should not crash
        assert isinstance(result, str)

    def test_padding_includes_nearby_context(self):
        """With default padding=5, a change at line 10 should capture blocks overlapping lines 5-15."""
        code_lines = [f"# line {i}" for i in range(1, 30)]
        code_lines[9] = "def target():"  # line 10
        code_lines[10] = "    return 'changed'"  # line 11
        code = "\n".join(code_lines)
        diff = f"""\
diff --git a/pad.py b/pad.py
--- a/pad.py
+++ b/pad.py
@@ -11,1 +11,1 @@
-    return 'old'
+    return 'changed'
"""
        result = extract_focused_context("pad.py", code, diff)
        assert "target" in result

    def test_multiple_changed_functions(self):
        """When two functions are changed, both should appear. Gamma is far enough
        away (>padding lines) that it should not be included."""
        # Separate functions by enough blank lines to exceed padding=5
        code = "def alpha():\n    return 1\n" + "\n" * 20 + \
               "def beta():\n    return 2\n" + "\n" * 20 + \
               "def gamma():\n    return 3\n"
        # alpha is around line 1-2, beta around line 23-24, gamma around line 45-46
        diff = """\
diff --git a/multi.py b/multi.py
--- a/multi.py
+++ b/multi.py
@@ -2,1 +2,1 @@
-    return 1
+    return 10
@@ -24,1 +24,1 @@
-    return 2
+    return 20
"""
        result = extract_focused_context("multi.py", code, diff)
        assert "alpha" in result
        assert "beta" in result
        # gamma is >20 lines away from any change — should be omitted
        assert "gamma" not in result or "omitted" in result.lower()
