"""Gate 2 — Test Gate (§09.5.4.10) with T3 sandbox and mutation testing.

The reviewed change is applied to an isolated git worktree; the project's
suite runs there — never in the user's checkout. An APPROVE-class verdict
cannot survive a failing (or unrunnable) suite.

Sandbox tiers:
- docker (T3, used automatically when the docker daemon is available):
  dependencies sync inside a container on the network, then the container
  is DISCONNECTED from the network before the suite runs — untrusted test
  code executes with no network and only the worktree mounted.
- subprocess (fallback): visible in the report as unsandboxed; acceptable
  for trusted repos only.

Mutation testing (deep mode, §08.2.2.6): mutmut mutates only the files the
diff changed; surviving mutants mean the tests don't actually constrain
the changed code. Score < 60% blocks APPROVE-class verdicts.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_TEST_TIMEOUT_S = 300
_MUTATION_TIMEOUT_S = 300
_DOCKER_IMAGE = "python:3.12-slim"
MUTATION_SCORE_MIN = 0.60


class MutationReport(BaseModel):
    status: Literal["passed", "failed", "skipped", "error"]
    summary: str
    killed: int = 0
    survived: int = 0
    survivors: list[str] = []

    @property
    def score(self) -> float | None:
        total = self.killed + self.survived
        return self.killed / total if total else None


class TestReport(BaseModel):
    status: Literal["passed", "failed", "no_tests", "skipped", "error"]
    summary: str
    detail: str = ""
    sandbox: str = "subprocess"
    mutation: MutationReport | None = None

    @property
    def gate_blocks(self) -> bool:
        # 'error' blocks too: an APPROVE issued without a runnable suite
        # would violate charter rule 9 (test before done). Found by the
        # self-review of PR #3.
        if self.status in ("failed", "error"):
            return True
        return bool(self.mutation and self.mutation.status == "failed")


def _run(cmd: list[str], cwd: str | Path, timeout: int = _TEST_TIMEOUT_S):
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout
    )


def _has_tests(root: Path) -> bool:
    return any(root.glob("tests/**/test_*.py")) or any(root.glob("test_*.py"))


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return _run(["docker", "info", "--format", "ok"], ".", timeout=10).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def run_test_gate(
    repo_dir: str, diff_raw: str, *, mode: str = "standard",
    changed_files: list[str] | None = None,
) -> TestReport:
    repo = Path(repo_dir).resolve()
    if not (repo / ".git").exists():
        return TestReport(
            status="skipped", summary="not a git repository; test gate skipped"
        )

    worktree = Path(tempfile.mkdtemp(prefix="autoproduct-testgate-"))
    try:
        return _run_gate_in_worktree(
            repo, worktree, diff_raw, mode=mode, changed_files=changed_files or []
        )
    except subprocess.TimeoutExpired as exc:
        return TestReport(status="error", summary=f"timed out: {str(exc)[:200]}")
    finally:
        _run(["git", "worktree", "remove", "--force", str(worktree)], repo)
        shutil.rmtree(worktree, ignore_errors=True)


def _run_gate_in_worktree(
    repo: Path, worktree: Path, diff_raw: str, *, mode: str, changed_files: list[str]
) -> TestReport:
    added = _run(["git", "worktree", "add", "--detach", str(worktree), "HEAD"], repo)
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
        return TestReport(status="no_tests", summary="no test files found in the project")

    use_docker = mode == "deep" and docker_available()
    if use_docker:
        report = _pytest_in_docker(worktree)
    else:
        report = _pytest_in_subprocess(worktree)

    if mode == "deep" and report.status == "passed":
        report.mutation = run_mutation(worktree, changed_files)
    return report


def _classify(returncode: int, output: str, *, sandbox: str) -> TestReport:
    tail = "\n".join(output.strip().splitlines()[-15:])
    if returncode == 0:
        return TestReport(status="passed", summary=_last_line(tail), detail=tail, sandbox=sandbox)
    if returncode == 5:  # pytest: no tests collected
        return TestReport(status="no_tests", summary="pytest collected no tests", sandbox=sandbox)
    return TestReport(status="failed", summary=_last_line(tail), detail=tail, sandbox=sandbox)


def _pytest_in_subprocess(worktree: Path) -> TestReport:
    if (worktree / "uv.lock").exists() and shutil.which("uv"):
        cmd = ["uv", "run", "--project", str(worktree), "pytest", "-q"]
    else:
        cmd = [sys.executable, "-m", "pytest", "-q"]
    proc = _run(cmd, worktree)
    return _classify(proc.returncode, proc.stdout or proc.stderr, sandbox="subprocess")


def _pytest_in_docker(worktree: Path) -> TestReport:
    """T3: sync dependencies with the network up, disconnect the container
    from every network, then run the suite — test code gets no network."""
    name = f"autoproduct-t3-{uuid.uuid4().hex[:8]}"
    try:
        created = _run(
            [
                "docker", "run", "-d", "--name", name,
                "-v", f"{worktree}:/work", "-w", "/work",
                "--memory", "2g", "--pids-limit", "512",
                _DOCKER_IMAGE, "sleep", "3600",
            ],
            worktree,
            timeout=60,
        )
        if created.returncode != 0:
            return _pytest_in_subprocess(worktree)  # sandbox unavailable → visible fallback
        if (worktree / "uv.lock").exists():
            sync_cmd = "pip install -q uv && uv sync --project /work --quiet"
            test_cmd = ["uv", "run", "--project", "/work", "--no-sync", "pytest", "-q"]
        else:
            sync_cmd = "pip install -q pytest"
            test_cmd = ["python", "-m", "pytest", "-q"]
        sync = _run(
            ["docker", "exec", name, "sh", "-c", sync_cmd],
            worktree,
            timeout=_TEST_TIMEOUT_S,
        )
        if sync.returncode != 0:
            return TestReport(
                status="error",
                summary="dependency sync failed inside sandbox",
                detail=(sync.stderr or sync.stdout)[-400:],
                sandbox="docker",
            )
        for net in ("bridge",):
            _run(["docker", "network", "disconnect", net, name], worktree, timeout=30)
        proc = _run(["docker", "exec", name, *test_cmd], worktree)
        return _classify(
            proc.returncode, proc.stdout or proc.stderr, sandbox="docker:no-network"
        )
    finally:
        _run(["docker", "rm", "-f", name], worktree, timeout=30)


_KILLED = re.compile(r"🎉 (\d+)")
# "no tests" counts as survived: mutmut 3 skips mutants no test exercises,
# and untested changed code is exactly what this gate exists to catch.
_SURVIVOR_LINE = re.compile(r"^\s{4}(\S+): (?:survived|no tests)", re.MULTILINE)


def run_mutation(worktree: Path, changed_files: list[str]) -> MutationReport:
    """Mutate only the .py source files the diff touched (§08.2.2.6)."""
    targets = [
        f for f in changed_files
        if f.endswith(".py") and not Path(f).name.startswith("test_")
        and "tests/" not in f and (worktree / f).exists()
    ]
    if not targets:
        return MutationReport(status="skipped", summary="no mutatable changed files")
    if not shutil.which("mutmut") and not _mutmut_in_env(worktree):
        return MutationReport(status="skipped", summary="mutmut not installed")

    pyproject = worktree / "pyproject.toml"
    config = "\n[tool.mutmut]\nsource_paths = [" + ", ".join(
        f'"{t}"' for t in targets
    ) + "]\n"
    pyproject.write_text(
        (pyproject.read_text(encoding="utf-8") if pyproject.exists() else "") + config,
        encoding="utf-8",
    )
    try:
        run = _run(_mutmut_cmd(worktree, "run"), worktree, timeout=_MUTATION_TIMEOUT_S)
        results = _run(_mutmut_cmd(worktree, "results"), worktree, timeout=60)
    except subprocess.TimeoutExpired:
        return MutationReport(
            status="error", summary=f"mutation run exceeded {_MUTATION_TIMEOUT_S}s"
        )
    killed_match = _KILLED.findall(run.stdout or "")
    killed = int(killed_match[-1]) if killed_match else 0
    survivors = _SURVIVOR_LINE.findall(results.stdout or "")
    report = MutationReport(
        status="passed",
        summary="",
        killed=killed,
        survived=len(survivors),
        survivors=survivors[:20],
    )
    if report.score is None:
        return MutationReport(
            status="error",
            summary="mutmut produced no mutants",
            survivors=[],
        )
    report.status = "passed" if report.score >= MUTATION_SCORE_MIN else "failed"
    report.summary = (
        f"mutation score {report.score:.0%} "
        f"({report.killed} killed / {report.survived} survived; bar {MUTATION_SCORE_MIN:.0%})"
    )
    return report


def _mutmut_in_env(worktree: Path) -> bool:
    return (worktree / "uv.lock").exists() and shutil.which("uv") is not None


def _mutmut_cmd(worktree: Path, subcommand: str) -> list[str]:
    if shutil.which("mutmut"):
        return ["mutmut", subcommand]
    return ["uv", "run", "--project", str(worktree), "mutmut", subcommand]


def _last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"
