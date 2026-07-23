"""tree-sitter symbol index (§09.7.1 `tree_sitter_query` / ADR upgrade to
the repo_graph toolset).

Structural, not lexical: definitions and references come from the parse
tree, so `apply_discount` the function is distinct from a string that
happens to contain it. Python-only in this iteration (the design's primary
language); other grammars slot into _LANGUAGES.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_python
from tree_sitter import Language, Parser, Query, QueryCursor

_PY_LANGUAGE = Language(tree_sitter_python.language())
_EXCLUDED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mas", "mutants"}
_MAX_FILES = 2000

_DEF_QUERY = Query(
    _PY_LANGUAGE,
    """
    (function_definition name: (identifier) @def.name)
    (class_definition name: (identifier) @def.name)
    (assignment left: (identifier) @def.name)
    """,
)
_REF_QUERY = Query(
    _PY_LANGUAGE,
    """
    (call function: (identifier) @ref.name)
    (call function: (attribute attribute: (identifier) @ref.name))
    (identifier) @any.name
    """,
)


def _iter_py_files(root: Path):
    count = 0
    for path in sorted(root.rglob("*.py")):
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        yield path
        count += 1
        if count >= _MAX_FILES:
            return


def symbol_refs(repo_dir: str | Path, symbol: str, max_results: int = 60) -> str:
    """Human/LLM-readable listing: definitions first, then call sites, then
    other identifier mentions. Used by the repo_graph voter via ToolBox."""
    root = Path(repo_dir).resolve()
    parser = Parser(_PY_LANGUAGE)
    definitions, calls, mentions = [], [], []

    for path in _iter_py_files(root):
        try:
            source = path.read_bytes()
        except OSError:
            continue
        tree = parser.parse(source)
        rel = path.relative_to(root)
        source_lines = source.splitlines()  # once per file (PR #15 review)

        def _line(node) -> str:
            row = node.start_point[0]
            text = source_lines[row].decode("utf-8", "replace").strip()
            return f"{rel}:{row + 1}: {text[:160]}"

        for _, captures in QueryCursor(_DEF_QUERY).matches(tree.root_node):
            for node in captures.get("def.name", []):
                if node.text.decode() == symbol:
                    definitions.append(_line(node))
        seen_rows = set()
        for _, captures in QueryCursor(_REF_QUERY).matches(tree.root_node):
            for kind in ("ref.name", "any.name"):
                for node in captures.get(kind, []):
                    if node.text.decode() != symbol:
                        continue
                    key = (str(rel), node.start_point[0])
                    if key in seen_rows:
                        continue
                    seen_rows.add(key)
                    (calls if kind == "ref.name" else mentions).append(_line(node))

    mentions = [m for m in mentions if m not in set(definitions) | set(calls)]
    sections = []
    if definitions:
        sections.append("definitions:\n" + "\n".join(definitions[:max_results]))
    if calls:
        sections.append("call sites:\n" + "\n".join(calls[:max_results]))
    if mentions:
        sections.append("other mentions:\n" + "\n".join(mentions[:max_results]))
    return "\n\n".join(sections) or f"(no occurrences of {symbol!r})"
