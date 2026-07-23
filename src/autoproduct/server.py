"""Webhook mode (§09.5) — the always-on entry points.

FastAPI receives GitHub PR events (Gate 1 entry) and incident POSTs
(Gate 6 entry) and launches the same CLI pipelines in detached worker
processes; results land in the usual mirrors and on GitHub. The SQLite
checkpointer gives crash-resumability per review.

Honest scope note (§08.2.2.12): the design's production posture is
Celery + Redis supervising LangGraph for failure detection across
instances. This single-process server is the CLI-parity webhook surface;
the Celery supervisor is additive when multi-instance operation matters.

Security: GitHub webhooks are verified with HMAC-SHA256
(AUTOPRODUCT_WEBHOOK_SECRET). Unsigned or mis-signed deliveries are
rejected before any parsing of the payload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request

REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


def _spawn(args: list[str], repo_dir: str) -> int:
    """Detached worker: the request cycle never blocks on a review.

    Deliberate exception to the subprocess timeout/capture convention: a
    worker outlives the request by design (reviews take minutes), its
    output lands in the `.mas/` mirrors, and its lifecycle belongs to the
    OS — a timeout here would kill in-flight reviews.
    """
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", "autoproduct.cli", *args],
        cwd=repo_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def _verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header.removeprefix("sha256="), expected)


def create_app(repo_dir: str = ".", *, spawn=_spawn) -> FastAPI:
    app = FastAPI(title="autoproduct", docs_url=None, redoc_url=None)
    repo = str(Path(repo_dir).resolve())

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/webhook/github", status_code=202)
    async def github_webhook(request: Request):
        secret = os.environ.get("AUTOPRODUCT_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(503, "AUTOPRODUCT_WEBHOOK_SECRET is not configured")
        body = await request.body()
        if not _verify_signature(
            secret, body, request.headers.get("X-Hub-Signature-256")
        ):
            raise HTTPException(401, "invalid webhook signature")

        event = request.headers.get("X-GitHub-Event", "")
        payload = json.loads(body or b"{}")
        if event != "pull_request" or payload.get("action") not in REVIEW_ACTIONS:
            return {"queued": False, "reason": f"ignored event {event}/{payload.get('action')}"}

        pr_url = payload.get("pull_request", {}).get("html_url")
        if not pr_url:
            raise HTTPException(422, "payload has no pull_request.html_url")
        pid = spawn(["review", pr_url], repo)
        return {"queued": True, "target": pr_url, "worker_pid": pid}

    @app.post("/incidents", status_code=202)
    async def incidents(request: Request):
        # Same trust bar as the GitHub webhook (PR #16 self-review finding):
        # incident intake mutates state and spawns work — bearer-token
        # authenticated with the shared secret.
        secret = os.environ.get("AUTOPRODUCT_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(503, "AUTOPRODUCT_WEBHOOK_SECRET is not configured")
        auth = request.headers.get("Authorization", "")
        if not (
            auth.startswith("Bearer ")
            and hmac.compare_digest(auth.removeprefix("Bearer "), secret)
        ):
            raise HTTPException(401, "missing or invalid bearer token")
        payload = await request.json()
        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(422, "incident needs a title")
        incident_id = uuid.uuid4().hex[:12]
        inbox = Path(repo) / ".mas" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        path = inbox / f"{incident_id}.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "id": incident_id,
                    "title": title,
                    "body": str(payload.get("body", "")),
                    "source": str(payload.get("source", "webhook")),
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        pid = spawn(["triage", str(path)], repo)
        return {"queued": True, "incident_id": incident_id, "worker_pid": pid}

    @app.get("/reviews")
    def reviews(limit: int = 50):
        # Sync handler (FastAPI threadpool) + bounded, newest-first listing
        # (PR #16 self-review: unbounded scan per request).
        reviews_dir = Path(repo) / ".mas" / "reviews"
        if not reviews_dir.is_dir():
            return []
        rows = []
        newest_first = sorted(
            (d for d in reviews_dir.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )[: max(1, min(limit, 500))]
        for review_dir in newest_first:
            final = sorted(review_dir.glob("[0-9]*-final.yaml"))
            if final:
                data = yaml.safe_load(final[-1].read_text(encoding="utf-8")) or {}
                rows.append(
                    {
                        "review_id": review_dir.name,
                        "verdict": data.get("verdict"),
                        "target": data.get("target"),
                        "finished_at": data.get("written_at"),
                    }
                )
            elif review_dir.is_dir():
                rows.append({"review_id": review_dir.name, "verdict": None})
        return rows

    @app.get("/reviews/{review_id}")
    def review_detail(review_id: str):
        from autoproduct.replay import load_replay, summarize_step

        try:
            rep = load_replay(Path(repo) / ".mas" / "reviews", review_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "review_id": rep.review_id,
            "verdict": rep.verdict,
            "duration_s": rep.duration_s,
            "steps": [
                {"node": s.node, "at": s.written_at.isoformat(), "summary": summarize_step(s)}
                for s in rep.steps
            ],
        }

    return app


def serve(repo_dir: str = ".", host: str = "127.0.0.1", port: int = 8422) -> None:
    import uvicorn

    uvicorn.run(create_app(repo_dir), host=host, port=port, log_level="info")
