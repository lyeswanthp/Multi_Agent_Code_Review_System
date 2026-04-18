"""AST-based Skeletonization for RAG context extraction.

Strips function and class bodies from source files while preserving
signatures, docstrings, and top-level module imports to reduce LLM token usage.
"""

from __future__ import annotations

from code_review.ast_extractor import _EXT_MAP, _get_parser

def extract_skeleton(filepath: str, content: str) -> str:
    """Extract a skeleton (signatures + docstrings only) from the source code."""
    ext = "." + filepath.rsplit(".", 1)[-1] if "." in filepath else ""
    lang_key = _EXT_MAP.get(ext)
    if not lang_key:
        # Fallback for unsupported languages (return first 50 lines to save tokens)
        lines = content.splitlines()
        return "\n".join(lines[:50]) + ("\n... [truncated]" if len(lines) > 50 else "")

    parser = _get_parser(lang_key)
    if not parser:
        return content

    source_bytes = content.encode("utf-8")
    try:
        tree = parser.parse(source_bytes)
    except Exception:
        return content

    replace_ranges = []

    def walk(node):
        if lang_key == "python":
            if node.type in ("function_definition", "class_definition"):
                body = node.child_by_field_name("body")
                if body:
                    start = body.start_byte
                    # Preserve docstring if present as the first expression statement
                    if body.children and body.children[0].type == "expression_statement":
                        expr = body.children[0]
                        if expr.children and expr.children[0].type == "string":
                            start = expr.end_byte
                    replace_ranges.append((start, body.end_byte))
        else: # js/ts
            if node.type in ("function_declaration", "method_definition", "arrow_function", "class_declaration"):
                body = node.child_by_field_name("body")
                if body:
                    try:
                        replace_ranges.append((body.start_byte + 1, body.end_byte - 1))
                    except Exception:
                        pass
        
        for child in node.children:
            walk(child)

    walk(tree.root_node)

    if not replace_ranges:
        return content

    replace_ranges.sort(key=lambda x: x[0])

    parts = []
    last_idx = 0
    for start, end in replace_ranges:
        if start > last_idx:
            parts.append(source_bytes[last_idx:start].decode("utf-8", errors="replace"))
        
        parts.append(" ...\n" if lang_key == "python" else "\n  ...\n")
        last_idx = end

    if last_idx < len(source_bytes):
        parts.append(source_bytes[last_idx:].decode("utf-8", errors="replace"))

    return "".join(parts).strip()
