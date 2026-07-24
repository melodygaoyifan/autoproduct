"""Multi-instance work queue — SQLite, zero new dependencies.

The webhook server used to Popen one detached worker per event: a burst
of PRs forks a burst of processes, nothing survives a host restart, and
a second instance can't share the load. This queue resolves that within
the repo's dependency rules: jobs land in a WAL-mode SQLite table, any
number of `autoproduct worker` processes on the host claim them
atomically, results are recorded, and unfinished jobs survive restarts
(a worker crash leaves the job visible as `running` with a dead pid —
`requeue_stale` returns it to the pool).

Scale-out boundary, stated honestly: SQLite serializes writers on ONE
host. Multiple hosts need a shared broker (Celery/Redis or Postgres) —
that swap replaces this module behind the same enqueue/claim/complete
surface and stays an infra decision.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from pydantic import BaseModel

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  argv TEXT NOT NULL,          -- JSON list of CLI args after `autoproduct`
  status TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|failed
  worker TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  created_at REAL NOT NULL,
  claimed_at REAL,
  finished_at REAL
);
"""


class Job(BaseModel):
    id: int
    kind: str
    argv: list[str]
    status: str
    worker: str = ""
    detail: str = ""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


def enqueue(db_path: str | Path, kind: str, argv: list[str]) -> int:
    import json

    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO jobs (kind, argv, created_at) VALUES (?, ?, ?)",
            (kind, json.dumps(argv), time.time()),
        )
        return int(cur.lastrowid)


def claim(db_path: str | Path, worker_id: str) -> Job | None:
    """Atomic claim: one UPDATE moves exactly one queued job to running —
    two workers can never claim the same job (immediate transaction)."""
    import json

    with _connect(db_path) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, kind, argv FROM jobs WHERE status='queued' "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute(
            "UPDATE jobs SET status='running', worker=?, claimed_at=? WHERE id=?",
            (worker_id, time.time(), row[0]),
        )
        conn.execute("COMMIT")
        return Job(id=row[0], kind=row[1], argv=json.loads(row[2]),
                   status="running", worker=worker_id)


def complete(db_path: str | Path, job_id: int, *, ok: bool, detail: str = "") -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET status=?, detail=?, finished_at=? WHERE id=?",
            ("done" if ok else "failed", detail[:500], time.time(), job_id),
        )


def requeue_stale(db_path: str | Path, *, max_age_s: float = 3600 * 6) -> int:
    """Jobs claimed by a worker that never finished (crash, restart) go
    back to the pool once stale — visible, never silently dropped."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='queued', worker='', detail='requeued: stale claim' "
            "WHERE status='running' AND claimed_at < ?",
            (time.time() - max_age_s,),
        )
        return cur.rowcount


def pending(db_path: str | Path) -> list[dict]:
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(r) for r in conn.execute(
                "SELECT id, kind, status, worker, detail, created_at FROM jobs "
                "ORDER BY id DESC LIMIT 100"
            )
        ]


def worker_loop(
    db_path: str | Path,
    repo_dir: str | Path,
    *,
    poll_s: float = 3.0,
    max_jobs: int | None = None,
    runner=None,
) -> int:
    """Claim → run `autoproduct <argv>` → record, forever (or max_jobs for
    tests). Run several of these processes for parallel throughput."""
    import subprocess
    import sys

    worker_id = f"{os.uname().nodename}:{os.getpid()}"
    done = 0
    while max_jobs is None or done < max_jobs:
        requeue_stale(db_path)
        job = claim(db_path, worker_id)
        if job is None:
            if max_jobs is not None:
                break
            time.sleep(poll_s)
            continue
        if runner is not None:
            ok, detail = runner(job)
        else:
            proc = subprocess.run(  # noqa: S603 — fixed argv prefix
                [sys.executable, "-m", "autoproduct.cli", *job.argv],
                cwd=repo_dir, capture_output=True, text=True, timeout=3600 * 4,
            )
            ok = proc.returncode == 0
            detail = (proc.stdout + proc.stderr)[-500:]
        complete(db_path, job.id, ok=ok, detail=detail)
        done += 1
    return done
