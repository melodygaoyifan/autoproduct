"""Trust-tier track record (§09.11.5).

Every deploy review is recorded; the human marks recommendations
correct/incorrect after the fact. When the last N PROMOTE recommendations
in a row were marked correct, the stage reports itself ELIGIBLE for the
assistive tier — the tier change itself is a human edit to
`.mas/deploy-policy.yaml`, never automatic. Production stays human-gated
regardless of any streak (§08.1.8 hard ceiling).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

DEFAULT_STREAK_NEEDED = 10


class Readiness(BaseModel):
    streak: int
    needed: int
    eligible: bool
    marked_total: int


def _path(repo_dir: str | Path) -> Path:
    return Path(repo_dir) / ".mas" / "deploy-track-record.yaml"


def _load(repo_dir: str | Path) -> list[dict]:
    path = _path(repo_dir)
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def _save(repo_dir: str | Path, records: list[dict]) -> None:
    path = _path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(records, sort_keys=False), encoding="utf-8")


def record_review(repo_dir: str | Path, review_id: str, verdict: str) -> None:
    records = _load(repo_dir)
    if any(r["review_id"] == review_id for r in records):
        return
    records.append({"review_id": review_id, "verdict": verdict, "outcome": None})
    _save(repo_dir, records)


def mark_outcome(repo_dir: str | Path, review_id: str, outcome: str) -> bool:
    if outcome not in ("correct", "incorrect"):
        raise ValueError("outcome must be 'correct' or 'incorrect'")
    records = _load(repo_dir)
    for record in records:
        if record["review_id"] == review_id:
            record["outcome"] = outcome
            _save(repo_dir, records)
            return True
    return False


def readiness(repo_dir: str | Path, needed: int = DEFAULT_STREAK_NEEDED) -> Readiness:
    """Streak = consecutive most-recent MARKED PROMOTE recommendations that
    were correct. Unmarked entries don't count either way; any 'incorrect'
    resets the streak to zero."""
    marked = [
        r for r in _load(repo_dir)
        if r["verdict"] == "PROMOTE" and r["outcome"] is not None
    ]
    streak = 0
    for record in reversed(marked):
        if record["outcome"] != "correct":
            break
        streak += 1
    return Readiness(
        streak=streak,
        needed=needed,
        eligible=streak >= needed,
        marked_total=len(marked),
    )
