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
    (re.compile(r"except\s*(Exception)?\s*:\s*pass"), "Swallowed exception", "P9", "high"),
    (re.compile(r"\beval\("), "eval() on untrusted input", "P6", "high"),
    (
        re.compile(r"f\"SELECT|SELECT .*(%s|\" *\+)", re.IGNORECASE),
        "SQL built by interpolation",
        "P6",
        "critical",
    ),
]

_DIFF_LINE = re.compile(r"^\+(?!\+\+)(.*)$", re.MULTILINE)
_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


@register
class MockProvider(Provider):
    name = "mock"

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
    ) -> str:
        return self.complete(
            model=model, system=system, user=messages[0]["content"], max_tokens=max_tokens
        )

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 4096) -> str:
        from autoproduct.compound import COMPOUND_MARKER
        from autoproduct.leader import LEADER_MARKER
        from autoproduct.maintenance.review import ROOTCAUSE_MARKER, TRIAGE_MARKER
        from autoproduct.verify import VERIFIER_MARKER

        if VERIFIER_MARKER in system:
            return self._verify(user)
        if LEADER_MARKER in system:
            return self._lead(user)
        if COMPOUND_MARKER in system:
            return self._compound(user)
        if TRIAGE_MARKER in system:
            priority = "P4" if "cosmetic" in user.lower() else "P2"
            return yaml.safe_dump(
                {"priority": priority, "category": "crash", "rationale": "mock triage"}
            )
        if ROOTCAUSE_MARKER in system:
            has_suspects = "score" in user
            return yaml.safe_dump(
                {
                    "hypothesis": "mock hypothesis from top suspect"
                    if has_suspects
                    else "insufficient evidence",
                    "confidence": 75 if has_suspects else 30,
                    "implicated_commit": None,
                    "implicated_files": [],
                    "next_action": "propose fix-PR",
                }
            )
        files = _FILE_HEADER.findall(user)
        file_path = files[0] if files else "unknown"
        findings = []
        for lineno, line in enumerate(_DIFF_LINE.findall(user), start=1):
            for pattern, title, taxonomy, severity in _PLANTED:
                if pattern.search(line):
                    findings.append(
                        {
                            "title": title,
                            "severity": severity,
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

    def _lead(self, user: str) -> str:
        """Cluster findings that share a file and overlap within 2 lines."""
        rows = re.findall(
            r"^(\d+)\. \[\w+\] (\S+?):(\d+)-(\d+)", user, re.MULTILINE
        )
        clusters: list[list[int]] = []
        placed: dict[int, list[int]] = {}
        parsed = [(int(n), path, int(a), int(b)) for n, path, a, b in rows]
        for n, path, a, b in parsed:
            for m, mpath, ma, mb in parsed:
                if m in placed and mpath == path and a <= mb + 2 and ma <= b + 2:
                    placed[m].append(n)
                    placed[n] = placed[m]
                    break
            if n not in placed:
                cluster = [n]
                placed[n] = cluster
                clusters.append(cluster)
        return yaml.safe_dump(
            {"clusters": clusters, "summary": "mock leader summary"}
        )

    def _compound(self, user: str) -> str:
        data = yaml.safe_load(user) or {}
        recurring = data.get("recurring_findings") or []
        proposals = [
            {
                "constraint": f"Do not reintroduce: {item['title']}",
                "rationale": f"seen {item['count']}x in the window",
            }
            for item in recurring[:2]
        ]
        return yaml.safe_dump({"proposals": proposals}, sort_keys=False)

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
