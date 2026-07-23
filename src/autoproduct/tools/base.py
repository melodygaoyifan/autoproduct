"""Deterministic tool runners (§09.7.3).

Tools run before voters; their findings are certain-by-construction and
enter the pipeline pre-verified (a compiler-grade check needs no fresh-agent
refutation). A tool whose binary is absent reports `skipped` — visible in
the mirror, never silently missing (no-silent-caps rule).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from autoproduct.diff import ParsedDiff
from autoproduct.state import VoterFinding


class ToolReport(BaseModel):
    tool: str
    status: Literal["ok", "skipped", "error"]
    detail: str = ""
    findings: list[VoterFinding] = Field(default_factory=list)


def tool_finding(
    tool: str,
    *,
    title: str,
    severity: str,
    file_path: str,
    line: int,
    evidence: str,
    explanation: str,
    confidence: str = "certain",
    taxonomy_hint: str | None = None,
) -> VoterFinding:
    return VoterFinding(
        voter=f"tool:{tool}",
        title=title,
        severity=severity,
        confidence=confidence,
        file_path=file_path,
        line_start=line,
        line_end=line,
        evidence=evidence,
        explanation=explanation,
        taxonomy_hint=taxonomy_hint or f"tool:{tool}",
        verification="VERIFIED",  # deterministic match, pre-verified
        score=100 if confidence == "certain" else 85,
    )


def run_all(diff: ParsedDiff, repo_dir: str) -> list[ToolReport]:
    from autoproduct.tools import external, probes

    def _wireup_on_changed(diff_: ParsedDiff, repo_dir_: str) -> ToolReport:
        """Repo-wide wireup scan, reported only for files this diff touched —
        pre-existing drift is not this PR's finding."""
        from autoproduct.tools.wireup import wireup_check

        report = wireup_check(repo_dir_)
        changed = set(diff_.changed_files)
        report.findings = [f for f in report.findings if f.file_path in changed]
        return report

    _wireup_on_changed.__name__ = "wireup_check"

    runners = [
        probes.secret_scan,
        probes.csrf_ssrf_probe,
        probes.slopsquat_check,
        _wireup_on_changed,
        external.semgrep,
        external.bandit,
        external.pip_audit,
        external.trufflehog,
    ]
    reports = []
    for runner in runners:
        try:
            reports.append(runner(diff, repo_dir))
        except Exception as exc:  # noqa: BLE001 — one broken tool never kills the run
            reports.append(
                ToolReport(
                    tool=runner.__name__,
                    status="error",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
    return reports


def render_for_context(reports: list[ToolReport]) -> str:
    """Compact summary injected into voter prompts — tool output feeds voter
    context (§08.2.2.8), it does not replace voter judgment."""
    lines = []
    for report in reports:
        if report.status != "ok" or not report.findings:
            continue
        for f in report.findings:
            lines.append(
                f"- [{report.tool}] {f.severity.value} {f.file_path}:{f.line_start}"
                f" {f.title} — evidence: {f.evidence[:120]}"
            )
    if not lines:
        return ""
    return "Deterministic tool findings (verified matches, cite as corroboration):\n" + "\n".join(lines)
