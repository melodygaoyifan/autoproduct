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
