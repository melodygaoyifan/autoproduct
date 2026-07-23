"""Incident ↔ recent-change correlation (§09.12).

Deterministic first pass: tokenize the incident text and score recent
commits by overlap with the files and symbols they touched. The RootCause
voter receives the ranked suspects as context — it investigates from
evidence, not from its priors (charter rule 3).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_STOPWORDS = {
    "error", "exception", "traceback", "failed", "failure", "none", "true",
    "false", "line", "file", "call", "recent", "most", "when", "after",
    "this", "that", "with", "from", "have", "self", "return", "raise",
}


@dataclass
class Suspect:
    sha: str
    subject: str
    files: list[str]
    score: int


def _tokens(text: str) -> set[str]:
    return {
        t.lower()
        for t in _TOKEN.findall(text)
        if t.lower() not in _STOPWORDS
    }


def recent_commits(repo_dir: str, days: int = 7, limit: int = 30) -> list[dict]:
    proc = subprocess.run(
        [
            "git", "log", f"--since={days} days ago", f"--max-count={limit}",
            "--name-only", "--pretty=format:%h%x09%s",
        ],
        cwd=repo_dir, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return []
    commits: list[dict] = []
    for block in proc.stdout.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines or "\t" not in lines[0]:
            continue
        sha, subject = lines[0].split("\t", 1)
        commits.append({"sha": sha, "subject": subject, "files": lines[1:]})
    return commits


def correlate(incident_text: str, repo_dir: str, days: int = 7) -> list[Suspect]:
    incident_tokens = _tokens(incident_text)
    suspects = []
    for commit in recent_commits(repo_dir, days=days):
        score = 0
        for path in commit["files"]:
            stem_tokens = _tokens(path.replace("/", " ").replace(".", " "))
            score += 3 * len(stem_tokens & incident_tokens)
        score += len(_tokens(commit["subject"]) & incident_tokens)
        if score > 0:
            suspects.append(
                Suspect(
                    sha=commit["sha"],
                    subject=commit["subject"],
                    files=commit["files"],
                    score=score,
                )
            )
    return sorted(suspects, key=lambda s: -s.score)[:5]
