"""Deterministic provider for tests and offline runs.

Emits a finding for every added line that matches one of the planted-bug
patterns in the fixture set, in the same YAML shape a real voter must emit.
This keeps the end-to-end graph test hermetic (no network, no keys).
"""

from __future__ import annotations

import re

import yaml

from autoproduct.providers.base import Provider, register

_PLANTED = [
    (re.compile(r"except\s*(Exception)?\s*:\s*pass"), "Swallowed exception", "P9"),
    (re.compile(r"\beval\("), "eval() on untrusted input", "P6"),
    (re.compile(r"SELECT .*(%s|\+|f\")", re.IGNORECASE), "SQL built by interpolation", "P6"),
]

_DIFF_LINE = re.compile(r"^\+(?!\+\+)(.*)$", re.MULTILINE)
_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


@register
class MockProvider(Provider):
    name = "mock"

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 4096) -> str:
        from autoproduct.verify import VERIFIER_MARKER

        if VERIFIER_MARKER in system:
            return self._verify(user)
        files = _FILE_HEADER.findall(user)
        file_path = files[0] if files else "unknown"
        findings = []
        for lineno, line in enumerate(_DIFF_LINE.findall(user), start=1):
            for pattern, title, taxonomy in _PLANTED:
                if pattern.search(line):
                    findings.append(
                        {
                            "title": title,
                            "severity": "high",
                            "confidence": "likely",
                            "file_path": file_path,
                            "line_start": lineno,
                            "line_end": lineno,
                            "evidence": line.strip(),
                            "explanation": f"Mock provider matched planted pattern: {title}",
                            "taxonomy_hint": taxonomy,
                        }
                    )
        return yaml.safe_dump({"status": "OK", "findings": findings}, sort_keys=False)

    def _verify(self, user: str) -> str:
        """Refute-by-quote: VERIFIED iff the claimed evidence text actually
        appears in the diff section of the prompt."""
        evidence_match = re.search(r"^evidence: (.+)$", user, re.MULTILINE)
        diff_match = re.search(r"<untrusted_diff>\n(.*)</untrusted_diff>", user, re.DOTALL)
        verified = bool(
            evidence_match
            and diff_match
            and evidence_match.group(1).strip() in diff_match.group(1)
        )
        return yaml.safe_dump(
            {
                "verdict": "VERIFIED" if verified else "NOT_REPRODUCIBLE",
                "reason": "mock quote check",
            }
        )
