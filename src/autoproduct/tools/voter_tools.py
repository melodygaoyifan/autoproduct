"""Read-only investigation tools for voters (§09.7.1).

Primitive tools + good context beat bespoke tool zoos (§08.2.2.8). All
tools here are risk L0-L1: read-only, repo-scoped, size-capped. The
allowlist comes from the voter's spec frontmatter; a tool outside the
allowlist does not exist for that voter — structurally, not by policy.
"""

from __future__ import annotations

import re
from pathlib import Path

VOTER_TOOL_REGISTRY = {"read_file", "grep", "list_files", "symbol_refs"}

_EXCLUDED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mas"}
_RESULT_CHAR_CAP = 20_000


class ToolBudgetExceeded(Exception):
    pass


class ToolBox:
    """Per-voter-invocation toolbox: allowlist + call budget enforced here,
    at the boundary, not in the prompt."""

    def __init__(self, repo_dir: str | Path, allowed: list[str], budget: int = 10):
        self.root = Path(repo_dir).resolve()
        self.allowed = set(allowed) & VOTER_TOOL_REGISTRY
        self.budget = budget
        self.calls_made = 0

    @property
    def remaining(self) -> int:
        return self.budget - self.calls_made

    def call(self, tool: str, args: dict) -> str:
        if tool not in self.allowed:
            return f"error: tool {tool!r} is not in your allowlist {sorted(self.allowed)}"
        if self.calls_made >= self.budget:
            raise ToolBudgetExceeded(f"tool budget of {self.budget} calls exhausted")
        self.calls_made += 1
        try:
            result = getattr(self, f"_{tool}")(**(args or {}))
        except TypeError as exc:
            return f"error: bad arguments for {tool}: {exc}"
        except Exception as exc:  # noqa: BLE001 — tool errors go back as data
            return f"error: {type(exc).__name__}: {exc}"
        return result[:_RESULT_CHAR_CAP]

    def _resolve(self, path: str) -> Path:
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise PermissionError(f"path {path!r} escapes the repository root")
        return resolved

    def _read_file(self, path: str, start: int = 1, limit: int = 200) -> str:
        resolved = self._resolve(path)
        if not resolved.is_file():
            return f"error: {path} is not a file"
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        window = lines[max(start - 1, 0) : max(start - 1, 0) + limit]
        numbered = "\n".join(
            f"{i}\t{line}" for i, line in enumerate(window, start=max(start, 1))
        )
        return numbered or f"(empty range; file has {len(lines)} lines)"

    def _iter_files(self, glob: str):
        for path in sorted(self.root.glob(glob)):
            if not path.is_file():
                continue
            if any(part in _EXCLUDED_DIRS for part in path.parts):
                continue
            yield path

    def _grep(self, pattern: str, glob: str = "**/*.py", max_results: int = 50) -> str:
        compiled = re.compile(pattern)
        hits = []
        for path in self._iter_files(glob):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    hits.append(f"{path.relative_to(self.root)}:{lineno}:{line.strip()}")
                    if len(hits) >= max_results:
                        return "\n".join(hits) + f"\n(capped at {max_results} results)"
        return "\n".join(hits) or "(no matches)"

    def _symbol_refs(self, symbol: str, max_results: int = 60) -> str:
        from autoproduct.tools.symbols import symbol_refs

        return symbol_refs(self.root, symbol, max_results=max_results)

    def _list_files(self, glob: str = "**/*", max_results: int = 200) -> str:
        paths = [
            str(p.relative_to(self.root)) for p in self._iter_files(glob)
        ][:max_results]
        return "\n".join(paths) or "(no files)"


TOOL_PROTOCOL_DOC = """
## Investigating before judging

You have read-only tools: {tools}. You have {budget} tool calls total.
To call one, respond with ONLY this YAML (no findings yet):

tool_request:
  tool: grep
  args: {{pattern: "def cancel_order", glob: "**/*.py"}}

Available tools and args:
- read_file: {{path, start (default 1), limit (default 200)}} — numbered lines
- grep: {{pattern (regex), glob (default "**/*.py"), max_results (default 50)}}
- list_files: {{glob (default "**/*"), max_results (default 200)}}
- symbol_refs: {{symbol, max_results (default 60)}} — tree-sitter-backed:
  definitions, call sites, and mentions of a Python symbol across the repo
  (prefer this over grep for signature-change impact)

Tool results arrive in the next message. When you have enough evidence (or
none of your tools would help), respond with the final status/findings YAML.
"""
