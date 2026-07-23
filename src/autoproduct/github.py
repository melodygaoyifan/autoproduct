"""GitHub side effects via `gh` — comment posting and HITL issues.

Failure-tolerant by design: an unreachable GitHub degrades the run to
local artifacts (the YAML mirror and review.md are always written); it
never fails the review.
"""

from __future__ import annotations

import re
import subprocess

PR_URL = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/\d+")


def _gh(args: list[str], cwd: str | None = None) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=60, cwd=cwd
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (proc.stdout or proc.stderr).strip()
    return proc.returncode == 0, output


def post_pr_comment(target: str, body: str) -> str | None:
    """Post when the target is a PR URL; returns an error note or None."""
    if not PR_URL.match(target):
        return "target is not a PR URL; comment not posted"
    ok, output = _gh(["pr", "comment", target, "--body", body])
    return None if ok else f"gh pr comment failed: {output[:200]}"


def create_issue(repo_dir: str, title: str, body: str) -> tuple[str | None, str | None]:
    """Open a HITL issue on the reviewed repo's origin. Returns
    (issue_url, error_note)."""
    ok, remote = _gh(["repo", "view", "--json", "url", "-q", ".url"], cwd=repo_dir)
    if not ok:
        return None, f"no GitHub remote detected ({remote[:120]}); issue not created"
    ok, output = _gh(
        ["issue", "create", "--title", title, "--body", body], cwd=repo_dir
    )
    if not ok:
        return None, f"gh issue create failed: {output[:200]}"
    return output.splitlines()[-1].strip(), None
