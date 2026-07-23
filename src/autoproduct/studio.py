"""Founder Studio — the browser UI for the FDR flow.

`autoproduct studio --repo-dir <workspace>` serves a single-page flow on
localhost: edit the FDR, get questions or the plain-language confirmation,
click 开始搭建 instead of typing --yes, watch progress, read the build
report. All state lives in the same workspace files the CLI writes — the
Studio is a veneer, never a second source of truth.

Local-first: binds 127.0.0.1, no external assets, no accounts. The build
runs as the same detached worker the CLI uses.
"""

from __future__ import annotations

import html
import subprocess
import sys
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

_STYLE = """
body{font-family:-apple-system,'PingFang SC',sans-serif;max-width:760px;
margin:2rem auto;padding:0 1rem;line-height:1.6;color:#1a1a1a}
textarea{width:100%;min-height:340px;font:14px/1.5 inherit;padding:.8rem;
border:1px solid #ccc;border-radius:8px;box-sizing:border-box}
button{background:#07c160;color:#fff;border:0;border-radius:8px;
padding:.7rem 1.6rem;font-size:1rem;cursor:pointer}
button.secondary{background:#576b95}
pre{white-space:pre-wrap;background:#f7f7f7;padding:1rem;border-radius:8px}
.card{border:1px solid #e5e5e5;border-radius:10px;padding:1rem 1.2rem;
margin:1rem 0}
.muted{color:#888;font-size:.9rem}
h1{font-size:1.4rem}
.ok{color:#07c160}.warn{color:#c87d2f}.bad{color:#d23}
"""


def _md(path: Path) -> str:
    return html.escape(path.read_text(encoding="utf-8")) if path.exists() else ""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>"
        f"<style>{_STYLE}</style><body><h1>{html.escape(title)}</h1>{body}"
    )


def _build_running(root: Path) -> bool:
    marker = root / ".mas" / "build.pid"
    if not marker.exists():
        return False
    try:
        pid = int(marker.read_text().strip())
    except ValueError:
        return False
    try:
        import os

        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _progress(root: Path) -> dict:
    plan_path = root / "product" / "plan.yaml"
    total = built = 0
    if plan_path.exists():
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
        total = len(plan.get("tasks", []))
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=root, capture_output=True, text=True
    ).stdout
    built = log.count("feat(")
    return {"total": total, "built": built, "running": _build_running(root)}


def create_studio_app(
    repo_dir: str | Path, *, spawn=None, provider: str = "anthropic"
) -> FastAPI:
    root = Path(repo_dir).resolve()
    app = FastAPI(title="autoproduct studio", docs_url=None, redoc_url=None)

    def _spawn_build() -> int:
        if spawn is not None:
            return spawn(root)
        proc = subprocess.Popen(  # noqa: S603 — fixed argv
            [sys.executable, "-m", "autoproduct.cli", "create", str(root),
             "--profile", _profile(root), "--yes"],
            cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        (root / ".mas").mkdir(exist_ok=True)
        (root / ".mas" / "build.pid").write_text(str(proc.pid), encoding="utf-8")
        return proc.pid

    def _profile(workspace: Path) -> str:
        data = yaml.safe_load(
            (workspace / ".mas" / "project.yaml").read_text(encoding="utf-8")
        )
        return data["profile"]

    @app.get("/", response_class=HTMLResponse)
    def home():
        fdr = root / "FDR.md"
        report = root / "product" / "BUILD-REPORT.md"
        confirmation = root / "product" / "CONFIRMATION.md"
        questions = root / "FDR-QUESTIONS.md"
        progress = _progress(root)

        if progress["running"]:
            done, total = progress["built"], progress["total"] or "?"
            return _page(
                "正在搭建 / Building…",
                f"<div class=card><p>已完成 {done} / {total} 个模块。"
                f"页面每 15 秒自动刷新。</p></div>"
                "<script>setTimeout(()=>location.reload(),15000)</script>",
            )
        if report.exists():
            return _page(
                "搭建报告 / Build report",
                f"<pre>{_md(report)}</pre>"
                "<form method=post action=/reset><button class=secondary>"
                "修改需求重新开始 / Edit FDR and rebuild</button></form>",
            )
        if confirmation.exists():
            return _page(
                "确认计划 / Confirm the plan",
                f"<pre>{_md(confirmation)}</pre>"
                "<form method=post action=/build><button>开始搭建 / Start building"
                "</button></form>"
                "<form method=post action=/reset style='margin-top:.5rem'>"
                "<button class=secondary>改需求 / Edit FDR</button></form>",
            )
        guide = _md(root / "FDR-GUIDE.md")
        question_block = (
            f"<div class=card><b class=warn>请先回答这些问题 / Please answer:"
            f"</b><pre>{_md(questions)}</pre></div>"
            if questions.exists()
            else ""
        )
        from autoproduct.upstream.fdr import TEMPLATE

        current = fdr.read_text(encoding="utf-8") if fdr.exists() else TEMPLATE
        return _page(
            "写下你的产品需求 / Describe your product",
            f"{question_block}"
            f"<form method=post action=/fdr>"
            f"<textarea name=fdr>{html.escape(current)}</textarea>"
            f"<p><button>检查并生成计划 / Check &amp; make the plan</button></p>"
            f"</form>"
            f"<details><summary class=muted>怎么写好？/ How to write a good FDR"
            f"</summary><pre>{guide}</pre></details>",
        )

    @app.post("/fdr")
    async def save_fdr(request: Request):
        form = await request.form()
        (root / "FDR.md").write_text(str(form.get("fdr", "")), encoding="utf-8")
        for stale in ("FDR-QUESTIONS.md",):
            (root / stale).unlink(missing_ok=True)
        from autoproduct.upstream.autopilot import run_autopilot

        run_autopilot(root, root / "FDR.md", yes=False, provider=provider)
        return RedirectResponse("/", status_code=303)

    @app.post("/build")
    def build():
        if not _build_running(root):
            _spawn_build()
        return RedirectResponse("/", status_code=303)

    @app.post("/reset")
    def reset():
        for stale in ("product/CONFIRMATION.md", "product/BUILD-REPORT.md", "FDR-QUESTIONS.md"):
            (root / stale).unlink(missing_ok=True)
        return RedirectResponse("/", status_code=303)

    @app.get("/status")
    def status():
        return JSONResponse(_progress(root))

    return app


def serve_studio(repo_dir: str | Path, host: str = "127.0.0.1", port: int = 8433) -> None:
    import uvicorn

    uvicorn.run(create_studio_app(repo_dir), host=host, port=port, log_level="warning")
