"""Availability-gated wrappers for external analyzers.

Each wrapper: if the binary isn't on PATH, report `skipped` with the
install hint. Findings are filtered to lines the diff actually added —
pre-existing debt is not this PR's finding.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from autoproduct.diff import ParsedDiff
from autoproduct.tools.base import ToolReport, tool_finding

_SEVERITY_MAP = {
    "ERROR": "high", "WARNING": "medium", "INFO": "low",       # semgrep
    "HIGH": "high", "MEDIUM": "medium", "LOW": "low",          # bandit
}


def _added_lines(diff: ParsedDiff) -> dict[str, set[int]]:
    return {f.path: {lineno for lineno, _ in f.added} for f in diff.files}


def _is_test_file(path: str) -> bool:
    name = Path(path).name
    return "tests/" in path or name.startswith("test_") or name.endswith("_test.py")


def _skipped(tool: str, hint: str) -> ToolReport:
    return ToolReport(tool=tool, status="skipped", detail=f"not installed ({hint})")


def _run_json(cmd: list[str], cwd: str, timeout: int = 300) -> tuple[str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    return proc.stdout, proc.stderr


def semgrep(diff: ParsedDiff, repo_dir: str) -> ToolReport:
    if not shutil.which("semgrep"):
        return _skipped("semgrep", "pip install semgrep")
    py_files = [p for p in diff.changed_files if Path(repo_dir, p).exists()]
    if not py_files:
        return ToolReport(tool="semgrep", status="ok", detail="no existing changed files")
    stdout, stderr = _run_json(
        ["semgrep", "--config", "auto", "--json", "--quiet", *py_files], repo_dir
    )
    if not stdout:
        return ToolReport(tool="semgrep", status="error", detail=stderr[:300])
    added = _added_lines(diff)
    findings = []
    for result in json.loads(stdout).get("results", []):
        path = result["path"]
        line = result["start"]["line"]
        if line not in added.get(path, set()):
            continue
        findings.append(
            tool_finding(
                "semgrep",
                title=result["check_id"].rsplit(".", 1)[-1],
                severity=_SEVERITY_MAP.get(result["extra"]["severity"], "medium"),
                file_path=path,
                line=line,
                evidence=result["extra"].get("lines", "")[:200],
                explanation=result["extra"].get("message", ""),
                taxonomy_hint="P6",
            )
        )
    return ToolReport(tool="semgrep", status="ok", findings=findings)


def bandit(diff: ParsedDiff, repo_dir: str) -> ToolReport:
    if not shutil.which("bandit"):
        return _skipped("bandit", "pip install bandit")
    py_files = [
        p for p in diff.changed_files
        if p.endswith(".py") and Path(repo_dir, p).exists()
    ]
    if not py_files:
        return ToolReport(tool="bandit", status="ok", detail="no existing changed .py files")
    stdout, stderr = _run_json(["bandit", "-f", "json", "-q", *py_files], repo_dir)
    if not stdout:
        return ToolReport(tool="bandit", status="error", detail=stderr[:300])
    added = _added_lines(diff)
    findings = []
    for result in json.loads(stdout).get("results", []):
        path = result["filename"].removeprefix("./")
        line = result["line_number"]
        if line not in added.get(path, set()):
            continue
        if result["test_id"] == "B101" and _is_test_file(path):
            continue  # assert is the pytest idiom, not a finding
        findings.append(
            tool_finding(
                "bandit",
                title=f"{result['test_id']}: {result['test_name']}",
                severity=_SEVERITY_MAP.get(result["issue_severity"], "medium"),
                file_path=path,
                line=line,
                evidence=result.get("code", "").strip()[:200],
                explanation=result["issue_text"],
                taxonomy_hint="P6",
            )
        )
    return ToolReport(tool="bandit", status="ok", findings=findings)


def pip_audit(diff: ParsedDiff, repo_dir: str) -> ToolReport:
    if not shutil.which("pip-audit"):
        return _skipped("pip_audit", "pip install pip-audit")
    req = Path(repo_dir) / "requirements.txt"
    if not any(f.path.endswith("requirements.txt") for f in diff.files) or not req.exists():
        return ToolReport(tool="pip_audit", status="ok", detail="no requirements.txt change")
    stdout, stderr = _run_json(
        ["pip-audit", "-r", str(req), "-f", "json", "--no-deps"], repo_dir, timeout=600
    )
    if not stdout:
        return ToolReport(tool="pip_audit", status="error", detail=stderr[:300])
    findings = []
    for dep in json.loads(stdout).get("dependencies", []):
        for vuln in dep.get("vulns", []):
            findings.append(
                tool_finding(
                    "pip_audit",
                    title=f"{dep['name']} {dep['version']}: {vuln['id']}",
                    severity="high",
                    file_path="requirements.txt",
                    line=1,
                    evidence=f"{dep['name']}=={dep['version']}",
                    explanation=vuln.get("description", "")[:400],
                    taxonomy_hint="P6",
                )
            )
    return ToolReport(tool="pip_audit", status="ok", findings=findings)


def trufflehog(diff: ParsedDiff, repo_dir: str) -> ToolReport:
    if not shutil.which("trufflehog"):
        return _skipped("trufflehog", "brew install trufflehog")
    stdout, _ = _run_json(
        ["trufflehog", "filesystem", repo_dir, "--json", "--no-update"], repo_dir
    )
    added = _added_lines(diff)
    findings = []
    for line in stdout.splitlines():
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
        meta = (
            result.get("SourceMetadata", {}).get("Data", {}).get("Filesystem", {})
        )
        path = Path(meta.get("file", "")).name
        matched = next((p for p in added if p.endswith(path)), None)
        if not matched:
            continue
        findings.append(
            tool_finding(
                "trufflehog",
                title=f"Verified secret: {result.get('DetectorName', 'unknown')}",
                severity="critical",
                file_path=matched,
                line=int(meta.get("line", 1)) or 1,
                evidence=result.get("Redacted", "(redacted)"),
                explanation="TruffleHog detected credential material in a changed file.",
                taxonomy_hint="P6",
            )
        )
    return ToolReport(tool="trufflehog", status="ok", findings=findings)
