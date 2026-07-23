"""Deterministic deploy-surface probes (§09.11).

Same posture as the review-stage tools: compiler-grade pattern checks run
before any voter, and their findings enter pre-verified. Two scans cover
the two highest-consequence deploy failure classes:

- migration_scan: destructive DDL/DML in migration-shaped files
- workflow_scan: CI/CD configs that widen permissions or run untrusted code
"""

from __future__ import annotations

import re

from autoproduct.diff import ParsedDiff
from autoproduct.tools.base import ToolReport, tool_finding

_DEPLOY_PATH = re.compile(
    r"(\.github/workflows/|Dockerfile|docker-compose|\.gitlab-ci|Jenkinsfile"
    r"|terraform|\.tf$|helm/|k8s/|kubernetes/|migrations?/|alembic/"
    r"|rollout|canary|deploy)",
    re.IGNORECASE,
)

_MIGRATION_PATH = re.compile(r"(migrations?/|alembic/|\.sql$)", re.IGNORECASE)

_DESTRUCTIVE_SQL = [
    (re.compile(r"\bDROP\s+(TABLE|COLUMN|DATABASE|SCHEMA)\b", re.I), "DROP statement"),
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE statement"),
    (re.compile(r"\bDELETE\s+FROM\s+\w+\s*;?\s*$", re.I), "DELETE without WHERE"),
    (re.compile(r"\bALTER\s+TABLE\b.*\bDROP\b", re.I), "ALTER TABLE ... DROP"),
    (re.compile(r"drop_column|drop_table|drop_constraint", re.I), "destructive migration op"),
]

_WORKFLOW_RISKS = [
    (re.compile(r"permissions:\s*write-all"), "workflow requests write-all permissions"),
    (re.compile(r"pull_request_target"), "pull_request_target trigger (runs with secrets on fork PRs)"),
    (re.compile(r"\bcurl\b.*\|\s*(ba)?sh"), "pipe-to-shell install in CI"),
    (re.compile(r"--privileged"), "privileged container"),
    (re.compile(r"secrets\.\w+.*(echo|cat|print)", re.I), "secret echoed to logs"),
]


def detect_deploy_files(changed_files: list[str]) -> list[str]:
    return [p for p in changed_files if _DEPLOY_PATH.search(p)]


def migration_scan(diff: ParsedDiff, repo_dir: str) -> ToolReport:
    findings = []
    for file in diff.files:
        if not _MIGRATION_PATH.search(file.path):
            continue
        for lineno, text in file.added:
            for pattern, label in _DESTRUCTIVE_SQL:
                if pattern.search(text):
                    findings.append(
                        tool_finding(
                            "migration_scan",
                            title=f"Destructive migration: {label}",
                            severity="critical",
                            file_path=file.path,
                            line=lineno,
                            evidence=text.strip()[:200],
                            explanation=f"{label} in a migration is irreversible in "
                            "production without a tested rollback path. Requires an "
                            "explicit expand/contract plan and human sign-off.",
                            taxonomy_hint="deploy:migration",
                        )
                    )
                    break
    return ToolReport(tool="migration_scan", status="ok", findings=findings)


_CANARY_KINDS = re.compile(r"^kind:\s*(Rollout|Canary)\s*$", re.MULTILINE)


def canary_scan(diff: ParsedDiff, repo_dir: str) -> ToolReport:
    """Static analysis of Argo Rollouts / Flagger manifests in the diff
    (§09.11): weakened analysis is a deploy risk even before any cluster
    integration exists. Signals, all from removed vs added lines:

    - analysis/steps removed without replacement
    - traffic step percentages raised
    - pause durations shortened or pauses removed
    - failure thresholds loosened
    """
    findings = []
    for file in diff.files:
        added_text = "\n".join(text for _, text in file.added)
        removed_text = "\n".join(file.removed)
        whole = added_text + "\n" + removed_text
        if not (_CANARY_KINDS.search(whole) or re.search(r"(rollout|canary)", file.path, re.I)):
            continue

        checks = [
            (
                r"(analysis|analysisTemplate|metrics):",
                "Canary analysis removed from rollout spec",
                "Automated analysis is the safety net of a progressive rollout; "
                "removing it makes promotion blind.",
            ),
            (
                r"-\s*pause:",
                "Rollout pause step removed",
                "Pause steps are the observation windows; without them the "
                "rollout promotes without bake time.",
            ),
        ]
        for pattern, title, explanation in checks:
            if re.search(pattern, removed_text) and not re.search(pattern, added_text):
                lineno = file.added[0][0] if file.added else 1
                findings.append(
                    tool_finding(
                        "canary_scan",
                        title=title,
                        severity="high",
                        file_path=file.path,
                        line=lineno,
                        evidence=(removed_text[:200] or "(removed lines)"),
                        explanation=explanation,
                        taxonomy_hint="deploy:canary",
                    )
                )

        def _numbers(pattern: str, text: str) -> list[int]:
            return [int(m) for m in re.findall(pattern, text)]

        removed_weights = _numbers(r"setWeight:\s*(\d+)", removed_text)
        added_weights = _numbers(r"setWeight:\s*(\d+)", added_text)
        if removed_weights and added_weights and min(added_weights) > min(removed_weights):
            findings.append(
                tool_finding(
                    "canary_scan",
                    title=f"Initial canary traffic raised "
                    f"{min(removed_weights)}% → {min(added_weights)}%",
                    severity="medium",
                    file_path=file.path,
                    line=file.added[0][0] if file.added else 1,
                    evidence=f"setWeight: {min(added_weights)}",
                    explanation="A larger first step exposes more users before any "
                    "analysis has run.",
                    confidence="likely",
                    taxonomy_hint="deploy:canary",
                )
            )
        removed_thresh = _numbers(r"(?:failureLimit|threshold):\s*(\d+)", removed_text)
        added_thresh = _numbers(r"(?:failureLimit|threshold):\s*(\d+)", added_text)
        if removed_thresh and added_thresh and max(added_thresh) > max(removed_thresh):
            findings.append(
                tool_finding(
                    "canary_scan",
                    title=f"Failure threshold loosened "
                    f"{max(removed_thresh)} → {max(added_thresh)}",
                    severity="high",
                    file_path=file.path,
                    line=file.added[0][0] if file.added else 1,
                    evidence=f"threshold: {max(added_thresh)}",
                    explanation="More failures are now tolerated before the rollout "
                    "aborts.",
                    confidence="likely",
                    taxonomy_hint="deploy:canary",
                )
            )
    return ToolReport(tool="canary_scan", status="ok", findings=findings)


def workflow_scan(diff: ParsedDiff, repo_dir: str) -> ToolReport:
    findings = []
    for file in diff.files:
        if not _DEPLOY_PATH.search(file.path):
            continue
        for lineno, text in file.added:
            for pattern, label in _WORKFLOW_RISKS:
                if pattern.search(text):
                    findings.append(
                        tool_finding(
                            "workflow_scan",
                            title=f"CI/CD risk: {label}",
                            severity="high",
                            file_path=file.path,
                            line=lineno,
                            evidence=text.strip()[:200],
                            explanation="This pattern widens the deploy blast radius or "
                            "the CI trust boundary; justify it in the PR or remove it.",
                            taxonomy_hint="deploy:cicd",
                        )
                    )
                    break
    return ToolReport(tool="workflow_scan", status="ok", findings=findings)
