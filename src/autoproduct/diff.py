"""Unified-diff acquisition and parsing.

The mode router (§08.3.5.1) and DoR gate (Gate 1) both operate on this
parsed representation, never on raw text, so their checks stay deterministic.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

_PR_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)")

# Signature-level heuristics used by the mode router. Deliberately
# conservative: matching any of these can only escalate the mode, never
# downgrade it.
_HIGH_RISK_PATH = re.compile(
    r"(auth|billing|payment|migration|secrets?|\.github/workflows|terraform|Dockerfile)",
    re.IGNORECASE,
)
_DEPENDENCY_FILES = {
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
    "package-lock.json",
}
_SAFETY_REMOVAL = re.compile(
    r"^-\s*(@(login_required|permission_required|csrf_protect|require_http_methods|"
    r"ratelimit|validate)|.*\b(sanitize|escape|verify|validate|authenticate)\w*\()",
)
_STATE_CHANGING_ENDPOINT = re.compile(
    r"^\+.*(@app\.(post|put|delete|patch)|methods=\[.*(POST|PUT|DELETE|PATCH))"
)


@dataclass
class FileDiff:
    path: str
    added: list[tuple[int, str]] = field(default_factory=list)  # (new lineno, text)
    removed: list[str] = field(default_factory=list)

    @property
    def changed_lines(self) -> int:
        return len(self.added) + len(self.removed)


@dataclass
class ParsedDiff:
    raw: str
    files: list[FileDiff] = field(default_factory=list)

    @property
    def changed_files(self) -> list[str]:
        return [f.path for f in self.files]

    @property
    def changed_lines(self) -> int:
        return sum(f.changed_lines for f in self.files)

    def touches_high_risk_paths(self) -> bool:
        return any(_HIGH_RISK_PATH.search(p) for p in self.changed_files)

    def adds_new_dependency(self) -> bool:
        return any(
            f.path.split("/")[-1] in _DEPENDENCY_FILES and f.added for f in self.files
        )

    def adds_state_changing_endpoint(self) -> bool:
        return any(
            _STATE_CHANGING_ENDPOINT.match(f"+{text}")
            for f in self.files
            for _, text in f.added
        )

    def has_safety_removal_signature(self) -> bool:
        return any(
            _SAFETY_REMOVAL.match(f"-{line}") for f in self.files for line in f.removed
        )

    def is_docs_only(self) -> bool:
        return bool(self.files) and all(
            p.endswith((".md", ".rst", ".txt")) for p in self.changed_files
        )

    def to_dict(self) -> dict:
        return {
            "changed_files": self.changed_files,
            "changed_lines": self.changed_lines,
            "raw": self.raw,
        }


def parse_unified_diff(text: str) -> ParsedDiff:
    parsed = ParsedDiff(raw=text)
    current: FileDiff | None = None
    new_lineno = 0
    for line in text.splitlines():
        if line.startswith("diff --git"):
            current = None
        elif line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path == "/dev/null":
                current = None
                continue
            current = FileDiff(path=path)
            parsed.files.append(current)
        elif line.startswith("@@") and current is not None:
            m = re.search(r"\+(\d+)", line)
            new_lineno = int(m.group(1)) if m else 1
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            current.added.append((new_lineno, line[1:]))
            new_lineno += 1
        elif current is not None and line.startswith("-") and not line.startswith("---"):
            current.removed.append(line[1:])
        elif current is not None and not line.startswith("\\"):
            new_lineno += 1
    return parsed


def fetch_diff(target: str, repo_dir: str = ".") -> ParsedDiff:
    """target is either a GitHub PR URL (fetched via `gh`) or a local git
    revision range such as `main...HEAD`."""
    if _PR_URL.match(target):
        out = subprocess.run(
            ["gh", "pr", "diff", target],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    else:
        out = subprocess.run(
            ["git", "diff", target],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_dir,
        ).stdout
    return parse_unified_diff(out)
