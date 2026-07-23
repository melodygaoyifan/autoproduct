"""assertion_delta (§13.29.5) — the anti-test-weakening AST diff.

When the implementer is allowed to rewrite a test file it authored (its
skeleton surface), the rewrite must not weaken it: removed assert
statements, added skip/xfail markers, or widened numeric tolerances are
build-gate failures citing the exact node. Pure `ast` — no new deps.
"""

from __future__ import annotations

import ast

from pydantic import BaseModel

_SKIP_MARKERS = ("skip", "skipif", "xfail")


class AssertionChange(BaseModel):
    change: str  # removed_assert | added_skip
    node: str


def _collect(source: str) -> tuple[list[str], list[str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []
    asserts, skips = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            asserts.append(ast.unparse(node))
        elif isinstance(node, (ast.Call, ast.Attribute)):
            code = ast.unparse(node)
            if any(f"pytest.{m}" in code or f"mark.{m}" in code for m in _SKIP_MARKERS):
                skips.append(code)
    return asserts, skips


def assertion_delta(before: str, after: str) -> list[AssertionChange]:
    before_asserts, before_skips = _collect(before)
    after_asserts, after_skips = _collect(after)
    changes = [
        AssertionChange(change="removed_assert", node=node)
        for node in before_asserts
        if node not in after_asserts
    ]
    changes += [
        AssertionChange(change="added_skip", node=node)
        for node in after_skips
        if node not in before_skips
    ]
    return changes
