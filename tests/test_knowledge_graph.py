"""Tests for the knowledge graph engine — AST extraction, graph building,
subgraph queries, and agent context generation."""

import pytest
import networkx as nx

from code_review.knowledge_graph import (
    build_file_graph,
    build_knowledge_graph,
    get_affected_subgraph,
    get_call_chain_context,
    get_security_context,
    get_graph_stats,
    _make_id,
    _extract_rationale_comments,
    NODE_MODULE,
    NODE_FUNCTION,
    NODE_CLASS,
    NODE_METHOD,
    NODE_RATIONALE,
    EDGE_CONTAINS,
    EDGE_IMPORTS,
    EDGE_CALLS,
    EDGE_INHERITS,
    EDGE_RATIONALE_FOR,
)


# ---------------------------------------------------------------------------
# _make_id
# ---------------------------------------------------------------------------

class TestMakeId:
    def test_simple(self):
        assert _make_id("app", "main") == "app_main"

    def test_strips_dots_and_underscores(self):
        assert _make_id("..foo..", "__bar__") == "foo_bar"

    def test_special_chars_replaced(self):
        assert _make_id("my-module", "func!") == "my_module_func"

    def test_lowercase(self):
        assert _make_id("MyClass") == "myclass"

    def test_empty_parts_skipped(self):
        assert _make_id("a", "", "b") == "a_b"


# ---------------------------------------------------------------------------
# _extract_rationale_comments
# ---------------------------------------------------------------------------

class TestExtractRationaleComments:
    def test_why_comment(self):
        result = _extract_rationale_comments("x = 1\n# WHY: legacy API requires this\ny = 2")
        assert len(result) == 1
        assert result[0] == (2, "WHY", "legacy API requires this")

    def test_hack_comment(self):
        result = _extract_rationale_comments("# HACK: workaround for upstream bug")
        assert result[0][1] == "HACK"

    def test_multiple_comments(self):
        code = "# NOTE: first\n# TODO: second\n# FIXME: third"
        result = _extract_rationale_comments(code)
        assert len(result) == 3
        assert [r[1] for r in result] == ["NOTE", "TODO", "FIXME"]

    def test_case_insensitive(self):
        result = _extract_rationale_comments("# why: test case insensitive")
        assert len(result) == 1
        assert result[0][1] == "WHY"

    def test_no_rationale(self):
        result = _extract_rationale_comments("# regular comment\nx = 1")
        assert result == []

    def test_security_tag(self):
        result = _extract_rationale_comments("# SECURITY: validate user input here")
        assert len(result) == 1
        assert result[0][1] == "SECURITY"


# ---------------------------------------------------------------------------
# build_file_graph — Python
# ---------------------------------------------------------------------------

SAMPLE_PY = """\
import os
from pathlib import Path

# WHY: legacy API compatibility
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
        # HACK: uppercase workaround
        if not self.ready:
            raise RuntimeError("not ready")
        return request.upper()

    def cleanup(self):
        self.ready = False
"""


class TestBuildFileGraphPython:
    def test_module_node_created(self):
        nodes, edges = build_file_graph("app.py", SAMPLE_PY)
        module_nodes = [n for n in nodes if n["type"] == NODE_MODULE and n["file"] == "app.py"]
        assert len(module_nodes) >= 1

    def test_functions_extracted(self):
        nodes, edges = build_file_graph("app.py", SAMPLE_PY)
        func_names = {n["label"] for n in nodes if n["type"] == NODE_FUNCTION}
        assert "connect()" in func_names
        assert "process()" in func_names

    def test_class_extracted(self):
        nodes, edges = build_file_graph("app.py", SAMPLE_PY)
        class_names = {n["label"] for n in nodes if n["type"] == NODE_CLASS}
        assert "Handler" in class_names

    def test_methods_extracted(self):
        nodes, edges = build_file_graph("app.py", SAMPLE_PY)
        method_names = {n["label"] for n in nodes if n["type"] == NODE_METHOD}
        assert "__init__()" in method_names
        assert "handle()" in method_names
        assert "cleanup()" in method_names

    def test_imports_extracted(self):
        nodes, edges = build_file_graph("app.py", SAMPLE_PY)
        import_edges = [e for e in edges if e["relation"] == EDGE_IMPORTS]
        import_targets = {e["target"] for e in import_edges}
        assert _make_id("os") in import_targets
        assert _make_id("pathlib") in import_targets

    def test_contains_edges(self):
        nodes, edges = build_file_graph("app.py", SAMPLE_PY)
        contains = [e for e in edges if e["relation"] == EDGE_CONTAINS]
        assert len(contains) > 0

    def test_call_edges(self):
        nodes, edges = build_file_graph("app.py", SAMPLE_PY)
        call_edges = [e for e in edges if e["relation"] == EDGE_CALLS]
        # connect() calls os.getenv → "getenv" should be a call target
        call_targets = {e["target"] for e in call_edges}
        assert any("getenv" in t for t in call_targets)

    def test_rationale_comments(self):
        nodes, edges = build_file_graph("app.py", SAMPLE_PY)
        rationale = [n for n in nodes if n["type"] == NODE_RATIONALE]
        assert len(rationale) >= 2  # WHY + HACK
        labels = [r["label"] for r in rationale]
        assert any("legacy API" in l for l in labels)
        assert any("uppercase" in l.lower() for l in labels)

    def test_rationale_linked_to_function(self):
        nodes, edges = build_file_graph("app.py", SAMPLE_PY)
        rat_edges = [e for e in edges if e["relation"] == EDGE_RATIONALE_FOR]
        assert len(rat_edges) >= 1

    def test_empty_file(self):
        nodes, edges = build_file_graph("empty.py", "")
        # Should just have a module node
        assert len(nodes) >= 1
        assert nodes[0]["type"] == NODE_MODULE

    def test_unsupported_language(self):
        nodes, edges = build_file_graph("data.csv", "a,b,c")
        assert nodes == []
        assert edges == []


# ---------------------------------------------------------------------------
# build_file_graph — JavaScript
# ---------------------------------------------------------------------------

SAMPLE_JS = """\
import { useState } from 'react';

function greet(name) {
    console.log('hello ' + name);
}

class App {
    render() {
        return greet('world');
    }
}
"""


class TestBuildFileGraphJS:
    def test_function_extracted(self):
        nodes, edges = build_file_graph("app.js", SAMPLE_JS)
        func_names = {n["label"] for n in nodes if n["type"] == NODE_FUNCTION}
        assert "greet()" in func_names

    def test_class_extracted(self):
        nodes, edges = build_file_graph("app.js", SAMPLE_JS)
        class_names = {n["label"] for n in nodes if n["type"] == NODE_CLASS}
        assert "App" in class_names

    def test_import_extracted(self):
        nodes, edges = build_file_graph("app.js", SAMPLE_JS)
        import_edges = [e for e in edges if e["relation"] == EDGE_IMPORTS]
        assert len(import_edges) > 0


# ---------------------------------------------------------------------------
# build_file_graph — Inheritance
# ---------------------------------------------------------------------------

class TestInheritance:
    def test_python_inheritance(self):
        code = "class Child(Parent):\n    pass\n"
        nodes, edges = build_file_graph("inherit.py", code)
        inherit_edges = [e for e in edges if e["relation"] == EDGE_INHERITS]
        assert len(inherit_edges) == 1
        assert "parent" in inherit_edges[0]["target"]

    def test_multiple_inheritance(self):
        code = "class Multi(Base1, Base2):\n    pass\n"
        nodes, edges = build_file_graph("multi.py", code)
        inherit_edges = [e for e in edges if e["relation"] == EDGE_INHERITS]
        assert len(inherit_edges) == 2


# ---------------------------------------------------------------------------
# build_knowledge_graph
# ---------------------------------------------------------------------------

class TestBuildKnowledgeGraph:
    def test_multi_file_graph(self):
        files = {
            "main.py": "import helper\ndef run():\n    helper.do_stuff()\n",
            "helper.py": "def do_stuff():\n    return 42\n",
        }
        G = build_knowledge_graph(files)
        assert isinstance(G, nx.DiGraph)
        assert G.number_of_nodes() > 0
        assert G.number_of_edges() > 0

    def test_empty_input(self):
        G = build_knowledge_graph({})
        assert G.number_of_nodes() == 0
        assert G.number_of_edges() == 0

    def test_single_file(self):
        G = build_knowledge_graph({"app.py": "def hello():\n    pass\n"})
        assert G.number_of_nodes() >= 2  # module + function

    def test_cross_file_imports(self):
        files = {
            "a.py": "from b import helper\n",
            "b.py": "def helper():\n    pass\n",
        }
        G = build_knowledge_graph(files)
        # There should be an import edge from a → b
        import_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("relation") == EDGE_IMPORTS]
        assert len(import_edges) > 0

    def test_unsupported_files_skipped(self):
        files = {
            "app.py": "def hello(): pass\n",
            "readme.md": "# Hello\n",
            "data.json": '{"key": "value"}',
        }
        G = build_knowledge_graph(files)
        # Only app.py should produce graph nodes
        py_nodes = [n for n, d in G.nodes(data=True) if d.get("file") == "app.py"]
        assert len(py_nodes) >= 1


# ---------------------------------------------------------------------------
# get_affected_subgraph
# ---------------------------------------------------------------------------

class TestGetAffectedSubgraph:
    def _build_test_graph(self):
        files = {
            "main.py": "import db\ndef run():\n    db.query()\n",
            "db.py": "def query():\n    return []\n\ndef connect():\n    pass\n",
            "utils.py": "def format_output(data):\n    return str(data)\n",
        }
        return build_knowledge_graph(files), ["main.py"]

    def test_returns_nodes_and_edges(self):
        G, changed = self._build_test_graph()
        result = get_affected_subgraph(G, changed)
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) > 0

    def test_changed_file_nodes_included(self):
        G, changed = self._build_test_graph()
        result = get_affected_subgraph(G, changed)
        files_in_subgraph = {n.get("file") for n in result["nodes"]}
        assert "main.py" in files_in_subgraph

    def test_neighbor_nodes_included(self):
        G, changed = self._build_test_graph()
        result = get_affected_subgraph(G, changed, max_hops=2)
        # db module should be reachable via imports
        node_ids = {n["id"] for n in result["nodes"]}
        assert any("db" in nid for nid in node_ids)

    def test_no_changed_files_returns_empty(self):
        G, _ = self._build_test_graph()
        result = get_affected_subgraph(G, ["nonexistent.py"])
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_max_hops_limits_traversal(self):
        G, changed = self._build_test_graph()
        result_1 = get_affected_subgraph(G, changed, max_hops=1)
        result_3 = get_affected_subgraph(G, changed, max_hops=3)
        assert len(result_1["nodes"]) <= len(result_3["nodes"])


# ---------------------------------------------------------------------------
# get_call_chain_context
# ---------------------------------------------------------------------------

class TestGetCallChainContext:
    def test_returns_string(self):
        files = {"app.py": "def run():\n    helper()\ndef helper():\n    pass\n"}
        G = build_knowledge_graph(files)
        result = get_call_chain_context(G, ["app.py"])
        assert isinstance(result, str)

    def test_contains_call_graph(self):
        files = {"app.py": "def run():\n    helper()\ndef helper():\n    pass\n"}
        G = build_knowledge_graph(files)
        result = get_call_chain_context(G, ["app.py"])
        assert "Call graph" in result or "Knowledge Graph" in result

    def test_empty_for_unknown_file(self):
        G = build_knowledge_graph({"a.py": "x = 1\n"})
        result = get_call_chain_context(G, ["unknown.py"])
        assert result == ""


# ---------------------------------------------------------------------------
# get_security_context
# ---------------------------------------------------------------------------

class TestGetSecurityContext:
    def test_returns_string(self):
        files = {"app.py": "def handle_request():\n    query_db()\ndef query_db():\n    pass\n"}
        G = build_knowledge_graph(files)
        result = get_security_context(G, ["app.py"])
        assert isinstance(result, str)

    def test_identifies_entry_points(self):
        files = {"app.py": "def main():\n    process()\ndef process():\n    pass\n"}
        G = build_knowledge_graph(files)
        result = get_security_context(G, ["app.py"])
        # main() is not called by anything → it should be an entry point
        assert "Entry point" in result or "entry point" in result.lower()

    def test_empty_for_unknown_file(self):
        G = build_knowledge_graph({"a.py": "x = 1\n"})
        result = get_security_context(G, ["unknown.py"])
        assert result == ""


# ---------------------------------------------------------------------------
# get_graph_stats
# ---------------------------------------------------------------------------

class TestGetGraphStats:
    def test_returns_expected_keys(self):
        G = build_knowledge_graph({"app.py": "def foo():\n    pass\n"})
        stats = get_graph_stats(G)
        assert "total_nodes" in stats
        assert "total_edges" in stats
        assert "node_types" in stats
        assert "edge_relations" in stats
        assert "god_nodes" in stats

    def test_node_type_counts(self):
        G = build_knowledge_graph({"app.py": "def foo():\n    pass\nclass Bar:\n    pass\n"})
        stats = get_graph_stats(G)
        assert stats["node_types"].get(NODE_MODULE, 0) >= 1
        assert stats["node_types"].get(NODE_FUNCTION, 0) >= 1

    def test_empty_graph(self):
        G = build_knowledge_graph({})
        stats = get_graph_stats(G)
        assert stats["total_nodes"] == 0
        assert stats["total_edges"] == 0

    def test_god_nodes_sorted_by_degree(self):
        files = {
            "hub.py": "import a\nimport b\nimport c\ndef hub_fn():\n    a_fn()\n    b_fn()\n    c_fn()\n",
            "a.py": "def a_fn(): pass\n",
            "b.py": "def b_fn(): pass\n",
            "c.py": "def c_fn(): pass\n",
        }
        G = build_knowledge_graph(files)
        stats = get_graph_stats(G)
        if stats["god_nodes"]:
            degrees = [gn["degree"] for gn in stats["god_nodes"]]
            assert degrees == sorted(degrees, reverse=True)


# ---------------------------------------------------------------------------
# Stress test
# ---------------------------------------------------------------------------

class TestStress:
    def test_many_functions(self):
        """50 functions in one file should all be extracted."""
        lines = [f"def func_{i}():\n    return {i}\n" for i in range(50)]
        code = "\n".join(lines)
        G = build_knowledge_graph({"big.py": code})
        func_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == NODE_FUNCTION]
        assert len(func_nodes) == 50

    def test_many_files(self):
        """20 files should all be processed."""
        files = {f"mod_{i}.py": f"def fn_{i}():\n    return {i}\n" for i in range(20)}
        G = build_knowledge_graph(files)
        module_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == NODE_MODULE and d.get("file")]
        assert len(module_nodes) == 20
