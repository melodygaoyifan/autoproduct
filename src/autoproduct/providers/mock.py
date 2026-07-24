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
            files_match = re.search(r"files: ([\w./-]+)", user)
            return yaml.safe_dump(
                {
                    "hypothesis": "mock hypothesis from top suspect"
                    if has_suspects
                    else "insufficient evidence",
                    "confidence": 75 if has_suspects else 30,
                    "implicated_commit": None,
                    "implicated_files": [files_match.group(1)] if files_match else [],
                    "next_action": "propose fix-PR",
                }
            )
        from autoproduct.maintenance.fixpr import FIXPR_MARKER

        if FIXPR_MARKER in system:
            return self._fixpr(user)
        from autoproduct.upstream.autopilot import REPORTER_MARKER
        from autoproduct.upstream.fdr import FDR_ASSESSOR_MARKER

        if FDR_ASSESSOR_MARKER in system:
            if "just an idea" in user:
                return yaml.safe_dump(
                    {"ready": False, "summary": "需要更多信息",
                     "questions": ["谁会用它？", "用户具体做什么？"]}
                )
            return yaml.safe_dump({"ready": True, "summary": "可以开始构建", "questions": []})
        if REPORTER_MARKER in system:
            return "mock 确认/报告：会做 X，不做 Y。(plain-language output)"
        from autoproduct.upstream.correction import CORRECTION_MARKER
        from autoproduct.upstream.telemetry import DIGEST_MARKER
        from autoproduct.upstream.walkthrough import WALKTHROUGH_MARKER

        if CORRECTION_MARKER in system:
            slug = re.search(r"slug: ([\w-]+)", user)
            kind = "scope_change" if "新增" in user else "fix"
            return yaml.safe_dump(
                {"spec_slug": slug.group(1) if slug else "unknown",
                 "kind": kind, "instruction": "apply the founder's correction"}
            )
        if WALKTHROUGH_MARKER in system:
            return "(mock: not a checklist)"  # forces the deterministic fallback
        if DIGEST_MARKER in system:
            return "# 本周 mock digest\n用户做了一些事。"
        from autoproduct.upstream.probegen import PROBEGEN_MARKER

        if PROBEGEN_MARKER in system:
            return yaml.safe_dump(
                {"probes": [
                    {"name": "root-responds",
                     "body": 's, d, _ = call("GET", "/")\n'
                             'assert s < 500, f"root returned {s}"'},
                    {"name": "bad-body-dropped", "body": "this is not python ("},
                ]},
                sort_keys=False,
            )
        from autoproduct.upstream.discover import BRIEF_CRITIC_MARKER, BRIEFWRITER_MARKER
        from autoproduct.upstream.plan import PLAN_CRITIC_MARKER, PLANNER_MARKER

        if BRIEFWRITER_MARKER in system:
            return yaml.safe_dump(
                {
                    "title": "Link sharing tool",
                    # Echo test markers from the idea so downstream mock
                    # stages (planner) can key on them via the brief.
                    "problem": "Sharing long URLs is unwieldy."
                    + (" make a cycle" if "make a cycle" in user else "")
                    + (" parallel plan" if "parallel plan" in user else ""),
                    "target_user": "Solo creators sharing links in chat.",
                    "hypotheses": [
                        {"statement": "Creators shorten >5 links/week", "evidence": "assumed"},
                        {"statement": "Click counts drive retention", "evidence": "sourced"},
                    ],
                    "scope_now": ["shorten a URL", "count clicks"],
                    "scope_later": ["custom domains"],
                    "scope_never": ["ads"],
                    "success_metrics": ["100 links created in week 1"],
                },
                sort_keys=False,
            )
        if BRIEF_CRITIC_MARKER in system:
            return yaml.safe_dump({"issues": []})
        if PLANNER_MARKER in system:
            if "parallel plan" in user:
                return yaml.safe_dump(
                    {"tasks": [
                        {"id": "t1", "title": "API base", "description": "an item store (api)",
                         "depends_on": [], "lane": "api", "estimate_hours": 3,
                         "files_expected": ["feature_t1.py"]},
                        {"id": "t2", "title": "UI base", "description": "an item store (ui)",
                         "depends_on": [], "lane": "ui", "estimate_hours": 3,
                         "files_expected": ["feature_t2.py"]},
                        {"id": "t3", "title": "Wire together", "description": "an item store (wire)",
                         "depends_on": ["t1", "t2"], "lane": "api", "estimate_hours": 2,
                         "files_expected": ["feature_t3.py"]},
                    ]},
                    sort_keys=False,
                )
            cyclic = "make a cycle" in user and "revision_feedback" not in user
            tasks = [
                {"id": "t1", "title": "URL store", "description": "an item store for links",
                 "depends_on": ["t2"] if cyclic else [], "lane": "api", "estimate_hours": 4},
                {"id": "t2", "title": "Shorten endpoint", "description": "POST /links",
                 "depends_on": ["t1"], "lane": "api", "estimate_hours": 4},
                {"id": "t3", "title": "Click counting", "description": "count redirects",
                 "depends_on": ["t2"], "lane": "api", "estimate_hours": 3},
            ]
            return yaml.safe_dump({"tasks": tasks}, sort_keys=False)
        if PLAN_CRITIC_MARKER in system:
            return yaml.safe_dump({"issues": []})
        from autoproduct.upstream.build import IMPLEMENTER_MARKER
        from autoproduct.upstream.spec import (
            AMBIGUITY_CRITIC_MARKER,
            SPECWRITER_MARKER,
            TESTABILITY_CRITIC_MARKER,
        )

        if SPECWRITER_MARKER in system:
            return self._spec(user)
        if TESTABILITY_CRITIC_MARKER in system or AMBIGUITY_CRITIC_MARKER in system:
            has_vague = "be fast" in user
            issues = (
                [{"severity": "major", "anchor": 0, "problem": "'fast' is untestable"}]
                if has_vague
                else []
            )
            return yaml.safe_dump({"issues": issues})
        if IMPLEMENTER_MARKER in system:
            return self._implement(user)
        from autoproduct.maintenance.skills_registry import SKILL_DRAFT_MARKER

        if SKILL_DRAFT_MARKER in system:
            return yaml.safe_dump(
                {
                    "name": "mock-recurring-class",
                    "description": "mock skill for a recurring incident class",
                    "body": "Check the usual suspect first.",
                },
                sort_keys=False,
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

    def _spec(self, user: str) -> str:
        """Canned item-store spec; emits a vague criterion on the first pass
        when the request asks for it, clean once revision feedback arrives.
        A task:<id> marker in the request uniquifies the title/tests so
        autopilot runs produce distinct specs per task."""
        task = re.search(r"task:([\w-]+)", user)
        suffix = f" {task.group(1)}" if task else ""
        vague_first_pass = "make it vague" in user and "revision_feedback" not in user
        criteria = [
            "When a client POSTs /items with a non-empty name, the system shall "
            "store the item and return its integer id.",
            "The system shall return all stored items, newest first, via GET /items.",
        ]
        if vague_first_pass:
            criteria[0] = "The system shall be fast when adding items."
        module = (
            "feature_" + re.sub(r"[^a-z0-9]", "_", task.group(1).lower())
            if task
            else "feature"
        )
        return yaml.safe_dump(
            {
                "title": f"Item store API{suffix}",
                "design": f"Single module `{module}.py` with an in-memory ItemStore; "
                "tests drive add() and list_items().",
                "criteria": criteria,
                "test_skeletons": [
                    {
                        "path": f"tests/test_{module}.py",
                        "purpose": "add returns id; list returns newest first",
                        "covers": [0, 1],
                    }
                ],
            },
            sort_keys=False,
        )

    def _implement(self, user: str) -> str:
        if "<complaint>" in user:
            match = re.search(r'<existing_file path="([^"]+)">\n(.*?)\n</existing_file>',
                              user, re.DOTALL)
            if not match:
                return yaml.safe_dump({"files": []})
            return yaml.safe_dump(
                {"files": [{"path": match.group(1),
                            "new_content": match.group(2) + "\n# corrected per founder\n"}]},
                sort_keys=False,
            )
        if "review_findings" in user:
            match = re.search(r'<file path="([^"]+)">\n(.*?)\n</file>', user, re.DOTALL)
            if not match:
                return yaml.safe_dump({"files": []})
            return yaml.safe_dump(
                {"files": [{"path": match.group(1),
                            "new_content": match.group(2) + "\n# reviewed\n"}]},
                sort_keys=False,
            )
        design = re.search(r"Single module `(feature[\w-]*)\.py`", user)
        module = design.group(1) if design else "feature"
        return yaml.safe_dump(
            {
                "files": [
                    {
                        "path": f"{module}.py",
                        "new_content": (
                            "class ItemStore:\n"
                            "    def __init__(self):\n"
                            "        self._items = []\n\n"
                            "    def add(self, name):\n"
                            "        if not name:\n"
                            "            raise ValueError('name required')\n"
                            "        item_id = len(self._items) + 1\n"
                            "        self._items.append({'id': item_id, 'name': name})\n"
                            "        return item_id\n\n"
                            "    def list_items(self):\n"
                            "        return list(reversed(self._items))\n"
                        ),
                    },
                    {
                        "path": f"tests/test_{module}.py",
                        "new_content": (
                            f"from {module} import ItemStore\n\n\n"
                            "def test_add_returns_id():\n"
                            "    store = ItemStore()\n"
                            "    assert store.add('a') == 1\n\n\n"
                            "def test_list_newest_first():\n"
                            "    store = ItemStore()\n"
                            "    store.add('a'); store.add('b')\n"
                            "    assert [i['name'] for i in store.list_items()] == ['b', 'a']\n"
                        ),
                    },
                ],
                "notes": "mock implementation",
            },
            sort_keys=False,
        )

    def _fixpr(self, user: str) -> str:
        """Fix the planted `return a - b` bug in the provided file, else abstain."""
        match = re.search(r'<file path="([^"]+)">\n(.*?)\n</file>', user, re.DOTALL)
        if not match or "return a - b" not in match.group(2):
            return yaml.safe_dump(
                {"files": [], "abstain_reason": "no known planted bug found"}
            )
        fixed = match.group(2).replace("return a - b", "return a + b")
        return yaml.safe_dump(
            {
                "files": [{"path": match.group(1), "new_content": fixed + "\n"}],
                "regression_test": {
                    "path": "tests/test_regression_mock.py",
                    "new_content": "from calc import add\n\n"
                    "def test_add_regression():\n    assert add(1, 2) == 3\n",
                },
                "commit_message": "fix: restore addition in add()",
                "abstain_reason": None,
            },
            sort_keys=False,
        )

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
