"""Knowledge Graph engine — AST-driven graph extraction inspired by Graphify.

Builds a lightweight NetworkX graph from source files using tree-sitter.
Extracts: modules, functions, classes, imports, call-graph edges, inheritance,
and developer-intent rationale comments (# WHY:, # HACK:, # NOTE:, etc.).

Each node/edge is tagged EXTRACTED (deterministic, from AST) with confidence 1.0.
The graph is queried to produce compact, agent-specific context subgraphs,
replacing naive regex-based import resolution with topology-aware traversal.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import networkx as nx
from tree_sitter import Language, Parser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Node / Edge type constants
# ---------------------------------------------------------------------------

NODE_MODULE = "module"
NODE_FUNCTION = "function"
NODE_CLASS = "class"
NODE_METHOD = "method"
NODE_RATIONALE = "rationale"

EDGE_CONTAINS = "contains"
EDGE_IMPORTS = "imports"
EDGE_CALLS = "calls"
EDGE_INHERITS = "inherits"
EDGE_RATIONALE_FOR = "rationale_for"

# ---------------------------------------------------------------------------
# Rationale comment extraction
# ---------------------------------------------------------------------------

_RATIONALE_RE = re.compile(
    r"#\s*(WHY|HACK|NOTE|IMPORTANT|TODO|FIXME|BUG|XXX|SECURITY|PERF)\s*:\s*(.+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Language setup (reuses tree-sitter parsers already in the project)
# ---------------------------------------------------------------------------

_PARSERS: dict[str, Parser] = {}

_EXT_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}


def _get_parser(lang_key: str) -> Parser | None:
    """Lazily create and cache a tree-sitter parser for the given language."""
    if lang_key in _PARSERS:
        return _PARSERS[lang_key]

    try:
        if lang_key == "python":
            import tree_sitter_python as tsp
            language = Language(tsp.language())
        elif lang_key == "javascript":
            import tree_sitter_javascript as tsjs
            language = Language(tsjs.language())
        elif lang_key == "typescript":
            import tree_sitter_typescript as tsts
            language = Language(tsts.language_typescript())
        else:
            return None

        parser = Parser(language)
        _PARSERS[lang_key] = parser
        return parser
    except Exception as e:
        logger.debug("Failed to create parser for %s: %s", lang_key, e)
        return None


def _lang_for_file(filepath: str) -> str | None:
    """Return the language key for a filepath, or None if unsupported."""
    ext = PurePosixPath(filepath).suffix
    return _EXT_MAP.get(ext)


def _read_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _make_id(*parts: str) -> str:
    """Build a stable node ID from one or more name parts."""
    combined = "_".join(p.strip("_.") for p in parts if p)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", combined)
    return cleaned.strip("_").lower()


# ---------------------------------------------------------------------------
# AST walking — extract nodes and edges from a single file
# ---------------------------------------------------------------------------

# Python-specific AST node types
_PY_CLASS_TYPES = {"class_definition"}
_PY_FUNC_TYPES = {"function_definition"}
_PY_IMPORT_TYPES = {"import_statement", "import_from_statement"}
_PY_CALL_TYPES = {"call"}

# JS/TS AST node types
_JS_CLASS_TYPES = {"class_declaration"}
_JS_FUNC_TYPES = {"function_declaration", "method_definition"}
_JS_IMPORT_TYPES = {"import_statement"}
_JS_CALL_TYPES = {"call_expression"}


def _get_lang_types(lang: str) -> tuple[set, set, set, set]:
    """Return (class_types, func_types, import_types, call_types) for a language."""
    if lang == "python":
        return _PY_CLASS_TYPES, _PY_FUNC_TYPES, _PY_IMPORT_TYPES, _PY_CALL_TYPES
    elif lang in ("javascript", "typescript"):
        return _JS_CLASS_TYPES, _JS_FUNC_TYPES, _JS_IMPORT_TYPES, _JS_CALL_TYPES
    return set(), set(), set(), set()


def _extract_name(node, source: bytes) -> str:
    """Extract the name identifier from a function/class AST node."""
    # Decorated definitions (Python)
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return _extract_name(child, source)

    name_node = node.child_by_field_name("name")
    if name_node:
        return _read_text(name_node, source)

    for child in node.children:
        if child.type in ("identifier", "property_identifier"):
            return _read_text(child, source)
    return "<anonymous>"


def _extract_calls(node, source: bytes, call_types: set, depth: int = 0) -> list[str]:
    """Recursively collect function/method call names from an AST subtree."""
    calls: list[str] = []
    if depth > 50:  # safety limit
        return calls

    if node.type in call_types:
        func_node = node.child_by_field_name("function")
        if func_node:
            if func_node.type in ("identifier",):
                calls.append(_read_text(func_node, source))
            elif func_node.type in ("attribute", "member_expression"):
                # e.g. self.method() or obj.method()
                attr = func_node.child_by_field_name("attribute") or func_node.child_by_field_name("property")
                if attr:
                    calls.append(_read_text(attr, source))
                else:
                    calls.append(_read_text(func_node, source))

    for child in node.children:
        calls.extend(_extract_calls(child, source, call_types, depth + 1))

    return calls


def _extract_imports_py(node, source: bytes) -> list[str]:
    """Extract imported module names from a Python import node."""
    modules = []
    if node.type == "import_statement":
        for child in node.children:
            if child.type in ("dotted_name", "aliased_import"):
                raw = _read_text(child, source)
                modules.append(raw.split(" as ")[0].strip().lstrip("."))
    elif node.type == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        if module_node:
            modules.append(_read_text(module_node, source).lstrip("."))
    return modules


def _extract_imports_js(node, source: bytes) -> list[str]:
    """Extract imported module names from a JS/TS import node."""
    modules = []
    for child in node.children:
        if child.type == "string":
            raw = _read_text(child, source).strip("'\"` ")
            module_name = raw.lstrip("./").split("/")[-1]
            if module_name:
                modules.append(module_name)
            break
    return modules


def _extract_inheritance_py(node, source: bytes) -> list[str]:
    """Extract base class names from a Python class definition."""
    bases = []
    args = node.child_by_field_name("superclasses")
    if args:
        for arg in args.children:
            if arg.type == "identifier":
                bases.append(_read_text(arg, source))
    return bases


def _extract_rationale_comments(content: str) -> list[tuple[int, str, str]]:
    """Extract developer-intent comments (# WHY:, # HACK:, etc.) with line numbers.

    Returns list of (line_number_1based, tag, text).
    """
    results = []
    for i, line in enumerate(content.splitlines(), start=1):
        m = _RATIONALE_RE.search(line)
        if m:
            results.append((i, m.group(1).upper(), m.group(2).strip()))
    return results


# ---------------------------------------------------------------------------
# Main graph builder
# ---------------------------------------------------------------------------

def build_file_graph(
    filepath: str,
    content: str,
) -> tuple[list[dict], list[dict]]:
    """Extract nodes and edges from a single source file.

    Returns (nodes, edges) where each is a list of dicts with:
      nodes: {id, label, type, file, line}
      edges: {source, target, relation, confidence, file, line}
    """
    lang = _lang_for_file(filepath)
    if not lang:
        return [], []

    parser = _get_parser(lang)
    if not parser:
        return [], []

    source_bytes = content.encode("utf-8")
    try:
        tree = parser.parse(source_bytes)
    except Exception as e:
        logger.debug("Parse failed for %s: %s", filepath, e)
        return [], []

    stem = PurePosixPath(filepath).stem
    class_types, func_types, import_types, call_types = _get_lang_types(lang)

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()

    def add_node(nid: str, label: str, ntype: str, line: int) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append({
                "id": nid, "label": label, "type": ntype,
                "file": filepath, "line": line,
            })

    def add_edge(src: str, tgt: str, relation: str, line: int) -> None:
        edges.append({
            "source": src, "target": tgt, "relation": relation,
            "confidence": "EXTRACTED", "file": filepath, "line": line,
        })

    # Module node
    module_nid = _make_id(stem)
    add_node(module_nid, PurePosixPath(filepath).name, NODE_MODULE, 1)

    def walk(node, parent_class_nid: str | None = None) -> None:
        t = node.type

        # Imports
        if t in import_types:
            if lang == "python":
                for mod in _extract_imports_py(node, source_bytes):
                    tgt_nid = _make_id(mod)
                    add_edge(module_nid, tgt_nid, EDGE_IMPORTS, node.start_point[0] + 1)
                    # Ensure target node exists (may be external)
                    if tgt_nid not in seen_ids:
                        seen_ids.add(tgt_nid)
                        nodes.append({
                            "id": tgt_nid, "label": mod, "type": NODE_MODULE,
                            "file": "", "line": 0,
                        })
            else:
                for mod in _extract_imports_js(node, source_bytes):
                    tgt_nid = _make_id(mod)
                    add_edge(module_nid, tgt_nid, EDGE_IMPORTS, node.start_point[0] + 1)
                    if tgt_nid not in seen_ids:
                        seen_ids.add(tgt_nid)
                        nodes.append({
                            "id": tgt_nid, "label": mod, "type": NODE_MODULE,
                            "file": "", "line": 0,
                        })
            return

        # Classes
        if t in class_types:
            class_name = _extract_name(node, source_bytes)
            class_nid = _make_id(stem, class_name)
            line = node.start_point[0] + 1
            add_node(class_nid, class_name, NODE_CLASS, line)
            add_edge(module_nid, class_nid, EDGE_CONTAINS, line)

            # Inheritance (Python)
            if lang == "python":
                for base in _extract_inheritance_py(node, source_bytes):
                    base_nid = _make_id(base)
                    if base_nid not in seen_ids:
                        seen_ids.add(base_nid)
                        nodes.append({
                            "id": base_nid, "label": base, "type": NODE_CLASS,
                            "file": "", "line": 0,
                        })
                    add_edge(class_nid, base_nid, EDGE_INHERITS, line)

            # Recurse into class body for methods
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    walk(child, parent_class_nid=class_nid)
            return

        # Functions / Methods
        if t in func_types:
            func_name = _extract_name(node, source_bytes)
            line = node.start_point[0] + 1

            if parent_class_nid:
                func_nid = _make_id(parent_class_nid, func_name)
                ntype = NODE_METHOD
                add_node(func_nid, f"{func_name}()", ntype, line)
                add_edge(parent_class_nid, func_nid, EDGE_CONTAINS, line)
            else:
                func_nid = _make_id(stem, func_name)
                ntype = NODE_FUNCTION
                add_node(func_nid, f"{func_name}()", ntype, line)
                add_edge(module_nid, func_nid, EDGE_CONTAINS, line)

            # Extract call-graph edges from function body
            body = node.child_by_field_name("body")
            if body:
                for callee in _extract_calls(body, source_bytes, call_types):
                    callee_nid = _make_id(stem, callee)
                    add_edge(func_nid, callee_nid, EDGE_CALLS, line)
            return

        # Decorated definitions (Python) — unwrap
        if t == "decorated_definition":
            for child in node.children:
                if child.type in func_types | class_types:
                    walk(child, parent_class_nid)
            return

        # Recurse into other nodes
        for child in node.children:
            walk(child, parent_class_nid)

    walk(tree.root_node)

    # Extract rationale comments
    for line_no, tag, text in _extract_rationale_comments(content):
        rat_nid = _make_id(stem, "rationale", str(line_no))
        add_node(rat_nid, f"[{tag}] {text}", NODE_RATIONALE, line_no)

        # Link rationale to the nearest function/class that contains it
        containing_node = None
        for n in nodes:
            if n["type"] in (NODE_FUNCTION, NODE_METHOD, NODE_CLASS) and n["file"] == filepath:
                if n["line"] <= line_no:
                    if containing_node is None or n["line"] > containing_node["line"]:
                        containing_node = n
        if containing_node:
            add_edge(rat_nid, containing_node["id"], EDGE_RATIONALE_FOR, line_no)
        else:
            add_edge(rat_nid, module_nid, EDGE_RATIONALE_FOR, line_no)

    return nodes, edges


def build_knowledge_graph(
    file_contents: dict[str, str],
) -> nx.DiGraph:
    """Build a directed knowledge graph from multiple source files.

    Args:
        file_contents: {filepath: content} dict (already read by context.py).

    Returns:
        A NetworkX DiGraph with all extracted nodes and edges.
    """
    G = nx.DiGraph()

    for filepath, content in file_contents.items():
        nodes, edges = build_file_graph(filepath, content)

        for node in nodes:
            G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})

        for edge in edges:
            G.add_edge(
                edge["source"], edge["target"],
                **{k: v for k, v in edge.items() if k not in ("source", "target")},
            )

    logger.info(
        "Knowledge graph built: %d nodes, %d edges from %d files",
        G.number_of_nodes(), G.number_of_edges(), len(file_contents),
    )
    return G


# ---------------------------------------------------------------------------
# Graph querying — extract agent-specific compact subgraphs
# ---------------------------------------------------------------------------

def get_affected_subgraph(
    G: nx.DiGraph,
    changed_files: list[str],
    max_hops: int = 2,
) -> dict:
    """Extract a compact subgraph centered on changed files.

    Traverses up to `max_hops` from any node belonging to a changed file.
    Returns a JSON-serializable dict with nodes and edges.
    """
    seed_nodes: set[str] = set()
    for node_id, attrs in G.nodes(data=True):
        if attrs.get("file") in changed_files:
            seed_nodes.add(node_id)

    if not seed_nodes:
        return {"nodes": [], "edges": [], "summary": "No graph nodes found for changed files."}

    # BFS up to max_hops
    visited: set[str] = set()
    frontier = seed_nodes.copy()
    for _ in range(max_hops):
        next_frontier: set[str] = set()
        for n in frontier:
            if n not in visited:
                visited.add(n)
                next_frontier.update(G.successors(n))
                next_frontier.update(G.predecessors(n))
        frontier = next_frontier - visited
    visited.update(frontier)

    # Build subgraph
    sub_nodes = []
    for nid in visited:
        if G.has_node(nid):
            attrs = dict(G.nodes[nid])
            attrs["id"] = nid
            sub_nodes.append(attrs)

    sub_edges = []
    for u, v, data in G.edges(data=True):
        if u in visited and v in visited:
            edge = dict(data)
            edge["source"] = u
            edge["target"] = v
            sub_edges.append(edge)

    return {"nodes": sub_nodes, "edges": sub_edges}


def get_call_chain_context(
    G: nx.DiGraph,
    changed_files: list[str],
) -> str:
    """Produce a compact text summary of the call chain around changed files.

    Used by the Logic Agent for understanding execution flow.
    """
    subgraph = get_affected_subgraph(G, changed_files, max_hops=2)
    if not subgraph["nodes"]:
        return ""

    parts = ["## Knowledge Graph Context\n"]

    # God nodes (high degree)
    degree_map = {}
    for n in subgraph["nodes"]:
        nid = n["id"]
        if G.has_node(nid):
            degree_map[nid] = G.degree(nid)
    if degree_map:
        god_nodes = sorted(degree_map, key=degree_map.get, reverse=True)[:5]
        god_labels = [G.nodes[n].get("label", n) for n in god_nodes if G.has_node(n)]
        if god_labels:
            parts.append(f"**Key nodes (highest connectivity):** {', '.join(god_labels)}\n")

    # Call chains
    call_edges = [e for e in subgraph["edges"] if e.get("relation") == EDGE_CALLS]
    if call_edges:
        parts.append("**Call graph:**")
        for e in call_edges[:20]:  # cap for token budget
            src_label = G.nodes[e["source"]].get("label", e["source"]) if G.has_node(e["source"]) else e["source"]
            tgt_label = G.nodes[e["target"]].get("label", e["target"]) if G.has_node(e["target"]) else e["target"]
            parts.append(f"  {src_label} → {tgt_label}")

    # Import graph
    import_edges = [e for e in subgraph["edges"] if e.get("relation") == EDGE_IMPORTS]
    if import_edges:
        parts.append("\n**Dependencies:**")
        for e in import_edges[:15]:
            src_label = G.nodes[e["source"]].get("label", e["source"]) if G.has_node(e["source"]) else e["source"]
            tgt_label = G.nodes[e["target"]].get("label", e["target"]) if G.has_node(e["target"]) else e["target"]
            parts.append(f"  {src_label} imports {tgt_label}")

    # Inheritance
    inherit_edges = [e for e in subgraph["edges"] if e.get("relation") == EDGE_INHERITS]
    if inherit_edges:
        parts.append("\n**Inheritance:**")
        for e in inherit_edges[:10]:
            src_label = G.nodes[e["source"]].get("label", e["source"]) if G.has_node(e["source"]) else e["source"]
            tgt_label = G.nodes[e["target"]].get("label", e["target"]) if G.has_node(e["target"]) else e["target"]
            parts.append(f"  {src_label} extends {tgt_label}")

    # Rationale nodes
    rationale_nodes = [n for n in subgraph["nodes"] if n.get("type") == NODE_RATIONALE]
    if rationale_nodes:
        parts.append("\n**Developer intent:**")
        for r in rationale_nodes[:10]:
            parts.append(f"  {r.get('file', '')}:L{r.get('line', 0)} — {r.get('label', '')}")

    return "\n".join(parts)


def get_security_context(
    G: nx.DiGraph,
    changed_files: list[str],
) -> str:
    """Produce a compact text summary focused on security-relevant patterns.

    Highlights entry points, data flow paths, and taint-propagation-relevant
    call chains. Used by the Security Agent.
    """
    subgraph = get_affected_subgraph(G, changed_files, max_hops=3)
    if not subgraph["nodes"]:
        return ""

    parts = ["## Security Graph Context\n"]

    # Entry point detection — functions that are not called by others
    called_functions: set[str] = set()
    callers: dict[str, list[str]] = {}
    for e in subgraph["edges"]:
        if e.get("relation") == EDGE_CALLS:
            called_functions.add(e["target"])
            callers.setdefault(e["target"], []).append(e["source"])

    functions_in_graph = {n["id"] for n in subgraph["nodes"]
                          if n.get("type") in (NODE_FUNCTION, NODE_METHOD)}
    entry_points = functions_in_graph - called_functions
    if entry_points:
        labels = []
        for ep in list(entry_points)[:10]:
            if G.has_node(ep):
                labels.append(f"{G.nodes[ep].get('label', ep)} ({G.nodes[ep].get('file', '')})")
        parts.append(f"**Entry points (uncalled functions):** {', '.join(labels)}\n")

    # Data flow: functions that call other functions (potential taint propagation)
    call_edges = [e for e in subgraph["edges"] if e.get("relation") == EDGE_CALLS]
    if call_edges:
        parts.append("**Data flow (call chains):**")
        for e in call_edges[:20]:
            src_label = G.nodes[e["source"]].get("label", e["source"]) if G.has_node(e["source"]) else e["source"]
            tgt_label = G.nodes[e["target"]].get("label", e["target"]) if G.has_node(e["target"]) else e["target"]
            parts.append(f"  {src_label} → {tgt_label}")

    # Import chains (potential external attack surface)
    import_edges = [e for e in subgraph["edges"] if e.get("relation") == EDGE_IMPORTS]
    if import_edges:
        parts.append("\n**External dependencies:**")
        for e in import_edges[:15]:
            tgt_label = G.nodes[e["target"]].get("label", e["target"]) if G.has_node(e["target"]) else e["target"]
            parts.append(f"  imports {tgt_label}")

    return "\n".join(parts)


def get_graph_stats(G: nx.DiGraph) -> dict:
    """Return summary statistics about the knowledge graph."""
    type_counts: dict[str, int] = {}
    for _, attrs in G.nodes(data=True):
        ntype = attrs.get("type", "unknown")
        type_counts[ntype] = type_counts.get(ntype, 0) + 1

    relation_counts: dict[str, int] = {}
    for _, _, attrs in G.edges(data=True):
        rel = attrs.get("relation", "unknown")
        relation_counts[rel] = relation_counts.get(rel, 0) + 1

    # God nodes (top 5 by degree)
    degree_sorted = sorted(G.degree(), key=lambda x: x[1], reverse=True)[:5]
    god_nodes = [
        {"id": nid, "label": G.nodes[nid].get("label", nid), "degree": deg}
        for nid, deg in degree_sorted
    ]

    return {
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
        "node_types": type_counts,
        "edge_relations": relation_counts,
        "god_nodes": god_nodes,
    }
