"""ears_lint (§13) — deterministic EARS grammar checking.

EARS (Easy Approach to Requirements Syntax) makes acceptance criteria
machine-checkable. Every criterion must match one of the five patterns,
and vague words that defer judgment to the implementer are rejected —
"fast" is not a requirement, "within 200ms" is.

Ubiquitous:    The <system> shall <response>.
Event-driven:  When <trigger>, the <system> shall <response>.
State-driven:  While <state>, the <system> shall <response>.
Unwanted:      If <condition>, then the <system> shall <response>.
Optional:      Where <feature is included>, the <system> shall <response>.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

_PATTERNS = [
    ("event", re.compile(r"^When .+?, the .+? shall .+", re.IGNORECASE)),
    ("state", re.compile(r"^While .+?, the .+? shall .+", re.IGNORECASE)),
    ("unwanted", re.compile(r"^If .+?, then the .+? shall .+", re.IGNORECASE)),
    ("optional", re.compile(r"^Where .+?, the .+? shall .+", re.IGNORECASE)),
    ("ubiquitous", re.compile(r"^The .+? shall .+", re.IGNORECASE)),
]

_VAGUE = re.compile(
    r"(?i)\b(fast|quick(ly)?|user-friendly|intuitive|appropriate(ly)?|"
    r"reasonable|robust|seamless(ly)?|efficient(ly)?|easy|simple|"
    r"as needed|etc\.?|and so on|properly|correctly|gracefully)\b"
)


class LintIssue(BaseModel):
    index: int
    criterion: str
    problem: str


def lint_criteria(criteria: list[str]) -> list[LintIssue]:
    issues = []
    for i, criterion in enumerate(criteria):
        text = criterion.strip().rstrip(".") + "."
        if not any(p.match(text) for _, p in _PATTERNS):
            issues.append(
                LintIssue(
                    index=i,
                    criterion=criterion,
                    problem="does not match any EARS pattern "
                    "(The/When/While/If-then/Where ... shall ...)",
                )
            )
        vague = _VAGUE.search(text)
        if vague:
            issues.append(
                LintIssue(
                    index=i,
                    criterion=criterion,
                    problem=f"vague term {vague.group(0)!r} — replace with a "
                    "measurable condition",
                )
            )
    return issues


def classify(criterion: str) -> str:
    text = criterion.strip()
    for name, pattern in _PATTERNS:
        if pattern.match(text):
            return name
    return "invalid"
