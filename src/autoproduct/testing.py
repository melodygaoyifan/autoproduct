"""Gate 2 — Test Gate (§09.5.4.10).

The reviewed change is applied to an isolated git worktree and the
project's test suite runs there — never in the user's checkout. An
APPROVE-class verdict cannot survive a failing suite.

Honest limitation (doc 10 Day 22.5): tests execute in a subprocess, not
yet the T3 container sandbox — acceptable for reviewing your own repos,
not for hostile code.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_TEST_TIMEOUT_S = 300


class TestReport(BaseModel):
    status: Literal["passed", "failed", "no_tests", "skipped", "error"]
    summary: str
    detail: str = ""

    @property
    def gate_blocks(self) -> bool:
        return self.status == "failed"


def _run(cmd: list[str], cwd: str | Path, timeout: int = _TEST_TIMEOUT_S):
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout
    )


def _has_tests(root: Path) -> bool:
    return any(root.glob("tests/**/test_*.py")) or any(root.glob("test_*.py"))


def run_test_gate(repo_dir: str, diff_raw: str) -> TestReport:
    repo = Path(repo_dir).resolve()
    if not (repo / ".git").exists():
        return TestReport(
            status="skipped", summary="not a git repository; test gate skipped"
        )

    worktree = Path(tempfile.mkdtemp(prefix="autoproduct-testgate-"))
    try:
        added = _run(
            ["git", "worktree", "add", "--detach", str(worktree), "HEAD"], repo
        )
        if added.returncode != 0:
            return TestReport(
                status="error",
                summary="could not create isolated worktree",
                detail=added.stderr[:400],
            )
        if diff_raw.strip():
            patch = worktree / ".autoproduct.patch"
            patch.write_text(diff_raw, encoding="utf-8")
            applied = _run(["git", "apply", "--3way", patch.name], worktree)
            patch.unlink(missing_ok=True)
            if applied.returncode != 0:
                return TestReport(
                    status="error",
                    summary="reviewed diff did not apply cleanly to HEAD",
                    detail=applied.stderr[:400],
                )
        if not _has_tests(worktree):
            return TestReport(
                status="no_tests", summary="no test files found in the project"
            )

        if (worktree / "uv.lock").exists() and shutil.which("uv"):
            cmd = ["uv", "run", "--project", str(worktree), "pytest", "-q"]
        else:
            cmd = [sys.executable, "-m", "pytest", "-q"]
        try:
            proc = _run(cmd, worktree)
        except subprocess.TimeoutExpired:
            return TestReport(
                status="error", summary=f"test run exceeded {_TEST_TIMEOUT_S}s"
            )
        tail = "\n".join((proc.stdout or proc.stderr).strip().splitlines()[-15:])
        if proc.returncode == 0:
            return TestReport(status="passed", summary=_last_line(tail), detail=tail)
        if proc.returncode == 5:  # pytest: no tests collected
            return TestReport(status="no_tests", summary="pytest collected no tests")
        return TestReport(status="failed", summary=_last_line(tail), detail=tail)
    finally:
        _run(["git", "worktree", "remove", "--force", str(worktree)], repo)
        shutil.rmtree(worktree, ignore_errors=True)


def _last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"
