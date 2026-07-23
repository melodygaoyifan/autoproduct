"""Deterministic mode router (§08.3.5.1) — conservative by design: any
ambiguity escalates toward `standard`; `fast` is reserved for diffs the
deterministic checks alone confirm are safety-irrelevant."""

from __future__ import annotations

from autoproduct.diff import ParsedDiff


def select_mode(diff: ParsedDiff, user_override: str | None = None) -> str:
    if user_override in ("fast", "standard", "deep"):
        return user_override

    if diff.touches_high_risk_paths():
        return "deep"

    if diff.adds_new_dependency():
        return "standard"
    if diff.adds_state_changing_endpoint():
        return "standard"
    if diff.changed_lines > 50 or len(diff.changed_files) > 3:
        return "standard"
    if diff.has_safety_removal_signature():
        return "standard"

    if diff.is_docs_only() and diff.changed_lines < 20 and len(diff.changed_files) < 2:
        return "fast"

    return "standard"
