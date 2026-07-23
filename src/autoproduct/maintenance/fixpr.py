"""Fix-PR generation (§09.12) — the maintenance assistive tier.

Trust-tier contract: this runs only when the human passes `--fix` (the
assistive approval), the fix is written in an isolated worktree branch,
the project's test suite must pass there before anything is pushed, and
the opened PR re-enters Code Review like any other PR. A fix that fails
its tests is abandoned and reported — never pushed.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from autoproduct import github
from autoproduct.maintenance.review import Incident, RootCauseResult
from autoproduct.providers import get_provider
from autoproduct.testing import (
    _pytest_in_docker,
    _pytest_in_subprocess,
    _run,
    docker_available,
)
from autoproduct.yamlx import extract_mapping

FIXPR_MARKER = "fix-PR author in a production-maintenance system"

_MAX_FILES = 3
_MAX_FILE_LINES = 400

_SYSTEM = f"""You are the {FIXPR_MARKER}. Produce the SMALLEST change that
fixes the root cause below. You are given the current content of the
implicated files; rewrite only what must change.

Rules:
- Touch at most {_MAX_FILES} files, only from the provided set.
- Return each touched file's COMPLETE new content — no diffs, no elisions.
- No drive-by refactors, no style changes, no new dependencies.
- If the hypothesis cannot be fixed within the provided files, return an
  empty files list and say why in `abstain_reason`.

Respond with ONLY YAML:
files:
  - path: ...
    new_content: |
      ...
commit_message: one line
abstain_reason: null or one sentence
"""


class FixAttempt(BaseModel):
    status: str  # opened | branch_only | tests_failed | abstained | error
    branch: str | None = None
    pr_url: str | None = None
    detail: str = ""
    files_changed: list[str] = Field(default_factory=list)


def _gather_sources(repo: Path, root_cause: RootCauseResult) -> dict[str, str]:
    sources = {}
    for rel in root_cause.implicated_files[:_MAX_FILES]:
        path = repo / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text.splitlines()) <= _MAX_FILE_LINES:
            sources[rel] = text
    return sources


def generate_fix_pr(
    incident: Incident,
    root_cause: RootCauseResult,
    *,
    repo_dir: str = ".",
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> FixAttempt:
    repo = Path(repo_dir).resolve()
    sources = _gather_sources(repo, root_cause)
    if not sources:
        return FixAttempt(
            status="abstained",
            detail="no implicated files available (missing, or above the "
            f"{_MAX_FILE_LINES}-line single-file cap)",
        )

    file_blocks = "\n\n".join(
        f"<file path=\"{path}\">\n{text}\n</file>" for path, text in sources.items()
    )
    user = (
        f"<incident>\n{incident.text}\n</incident>\n\n"
        f"<hypothesis confidence=\"{root_cause.confidence}\">\n"
        f"{root_cause.hypothesis}\n</hypothesis>\n\n{file_blocks}"
    )
    try:
        raw = get_provider(provider).complete(
            model=model, system=_SYSTEM, user=user, max_tokens=8192
        )
        data = extract_mapping(raw, ("files", "abstain_reason"))
    except Exception as exc:  # noqa: BLE001
        return FixAttempt(status="error", detail=f"{type(exc).__name__}: {exc}")

    files = data.get("files") or []
    if not files:
        return FixAttempt(
            status="abstained", detail=str(data.get("abstain_reason") or "model abstained")
        )
    if len(files) > _MAX_FILES or any(f["path"] not in sources for f in files):
        return FixAttempt(
            status="error",
            detail="model touched files outside the implicated set — refused",
        )

    branch = f"autoproduct/fix-{incident.id}"
    worktree = Path(tempfile.mkdtemp(prefix="autoproduct-fixpr-"))
    try:
        added = _run(
            ["git", "worktree", "add", "-B", branch, str(worktree), "HEAD"], repo
        )
        if added.returncode != 0:
            return FixAttempt(status="error", detail=added.stderr[:300])
        for f in files:
            (worktree / f["path"]).write_text(f["new_content"], encoding="utf-8")

        # The fix is LLM-generated code: verify it in the T3 sandbox when
        # available, same as Gate 2 (PR #11 self-review finding).
        report = (
            _pytest_in_docker(worktree)
            if docker_available()
            else _pytest_in_subprocess(worktree)
        )
        if report.status not in ("passed", "no_tests"):
            return FixAttempt(
                status="tests_failed",
                detail=f"fix abandoned, suite {report.status}: {report.summary}",
                files_changed=[f["path"] for f in files],
            )

        message = str(data.get("commit_message") or f"fix: {incident.title[:60]}")
        _run(["git", "add", "-A"], worktree)
        committed = _run(
            ["git", "commit", "-m",
             f"{message}\n\nProposed by autoproduct maintenance for incident "
             f"{incident.id}; re-enters code review like any PR."],
            worktree,
        )
        if committed.returncode != 0:
            return FixAttempt(status="error", detail=committed.stderr[:300])

        pushed = _run(["git", "push", "-u", "origin", branch, "--force-with-lease"], worktree)
        if pushed.returncode != 0:
            return FixAttempt(
                status="branch_only",
                branch=branch,
                detail=f"local branch created; push failed ({pushed.stderr.strip()[:120]})",
                files_changed=[f["path"] for f in files],
            )
        ok, output = github._gh(
            ["pr", "create", "--head", branch,
             "--title", f"[autoproduct fix] {incident.title[:80]}",
             "--body",
             f"Automated fix proposal for incident `{incident.id}`.\n\n"
             f"**Hypothesis** ({root_cause.confidence}% confidence): "
             f"{root_cause.hypothesis}\n\nSuite passed in the fix worktree. "
             "This PR re-enters code review like any other.\n\n"
             "🤖 Generated with [Claude Code](https://claude.com/claude-code)"],
            cwd=str(repo),
        )
        return FixAttempt(
            status="opened" if ok else "branch_only",
            branch=branch,
            pr_url=output.splitlines()[-1].strip() if ok else None,
            detail="" if ok else f"branch pushed; gh pr create failed: {output[:120]}",
            files_changed=[f["path"] for f in files],
        )
    finally:
        _run(["git", "worktree", "remove", "--force", str(worktree)], repo)
        shutil.rmtree(worktree, ignore_errors=True)
