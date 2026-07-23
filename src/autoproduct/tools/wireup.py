"""wireup_check — frontend ↔ backend contract drift (DAPLab P1/P5).

Deterministic: extract the routes the backend actually serves and the
endpoints the frontend actually calls, then report calls with no serving
route. UI drift that breaks the wireup is exactly the class that passes
unit tests and fails in the user's hands.

Backend route sources: FastAPI/Flask-style decorators, plus generic
route-table literals. Frontend call sites: fetch/axios/XMLHttpRequest in
js/ts, wx.request urls in 小程序 code, form actions in HTML.
Path params normalize to a wildcard so `/items/{id}`, `/items/:id` and
`/items/<int:id>` all match `/items/123`.
"""

from __future__ import annotations

import re
from pathlib import Path

from autoproduct.tools.base import ToolReport, tool_finding

_EXCLUDED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mas", "mutants", "specs"}

_BACKEND_PATTERNS = [
    re.compile(r"@\w+\.(?:get|post|put|delete|patch|route)\(\s*['\"](/[^'\"]*)"),
    re.compile(r"add_api_route\(\s*['\"](/[^'\"]*)"),
    re.compile(r"['\"](?:GET|POST|PUT|DELETE|PATCH)['\"]\s*,\s*['\"](/[^'\"]*)"),
    re.compile(r"path\s*==\s*['\"](/[^'\"]*)"),
    re.compile(r"startswith\(\s*['\"](/[^'\"]*)"),
]
_FRONTEND_PATTERNS = [
    re.compile(r"fetch\(\s*[`'\"](/[^`'\"\s?]*)"),
    re.compile(r"axios\.(?:get|post|put|delete|patch)\(\s*[`'\"](/[^`'\"\s?]*)"),
    re.compile(r"url:\s*[`'\"](?:https?://[^/]+)?(/[^`'\"\s?]*)"),  # wx.request / ajax configs
    re.compile(r"\.open\(\s*['\"][A-Z]+['\"]\s*,\s*[`'\"](/[^`'\"\s?]*)"),
    re.compile(r"action=[\"'](/[^\"'\s?]*)"),
]
_PARAM_SEGMENT = re.compile(r"^(\{.+\}|:.+|<.+>|\$\{.+\})$")

_BACKEND_SUFFIXES = (".py",)
_FRONTEND_SUFFIXES = (".js", ".ts", ".jsx", ".tsx", ".html", ".wxml", ".vue")


def _normalize(path: str) -> tuple:
    segments = [s for s in path.strip("/").split("/") if s]
    return tuple("*" if _PARAM_SEGMENT.match(s) else s for s in segments)


def _matches(call: tuple, route: tuple) -> bool:
    if len(call) != len(route):
        return False
    return all(r == "*" or c == "*" or c == r for c, r in zip(call, route))


def _iter_files(root: Path, suffixes: tuple):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in _EXCLUDED_DIRS or part == "tests" for part in path.parts):
            continue
        yield path


def collect_routes(root: Path) -> set[tuple]:
    routes: set[tuple] = set()
    for path in _iter_files(root, _BACKEND_SUFFIXES):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in _BACKEND_PATTERNS:
            for match in pattern.findall(text):
                routes.add(_normalize(match))
    return routes


def collect_calls(root: Path) -> list[tuple[str, int, str]]:
    calls = []
    for path in _iter_files(root, _FRONTEND_SUFFIXES):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in _FRONTEND_PATTERNS:
                for match in pattern.findall(line):
                    if match and not match.startswith("//"):
                        calls.append((str(path.relative_to(root)), lineno, match))
    return calls


def wireup_check(repo_dir: str | Path) -> ToolReport:
    root = Path(repo_dir).resolve()
    routes = collect_routes(root)
    findings = []
    if not routes:
        calls = collect_calls(root)
        if calls:
            return ToolReport(
                tool="wireup_check",
                status="ok",
                detail=f"{len(calls)} frontend call(s) but no recognizable backend "
                "routes — framework not understood or backend missing",
            )
        return ToolReport(tool="wireup_check", status="ok", detail="no frontend/backend surface")
    seen = set()
    for rel, lineno, call in collect_calls(root):
        normalized = _normalize(call)
        if any(_matches(normalized, r) for r in routes):
            continue
        key = (rel, call)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            tool_finding(
                "wireup_check",
                title=f"Frontend calls {call} but no backend route serves it",
                severity="high",
                file_path=rel,
                line=lineno,
                evidence=call,
                explanation="The UI references an endpoint the backend does not "
                "define — this passes unit tests and fails in the user's hands "
                "(P1 grounding mismatch). Add the route or fix the path.",
                taxonomy_hint="P1",
            )
        )
    return ToolReport(tool="wireup_check", status="ok", findings=findings)


def wireup_diff_gate(repo_dir: str | Path) -> ToolReport:
    """Same check, run as a build-gate step after the implementer writes."""
    return wireup_check(repo_dir)
