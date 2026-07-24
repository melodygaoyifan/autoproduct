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
from fastapi import FastAPI, HTTPException, Request
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


def _failed_tasks(root: Path) -> list[str]:
    path = root / "product" / "outcomes.yaml"
    if not path.exists():
        return []
    outcomes = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [o["task_id"] for o in outcomes if o.get("status") != "built"]


def _pending_feature(root: Path) -> Path | None:
    features_dir = root / "product" / "features"
    if not features_dir.is_dir():
        return None
    for d in sorted(features_dir.iterdir(), reverse=True):
        if (d / "CONFIRMATION.md").exists() and not (d / "REPORT.md").exists():
            return d
    return None


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
        ["git", "log", "--oneline"], cwd=root, capture_output=True, timeout=60, text=True
    ).stdout
    built = log.count("feat(")
    return {"total": total, "built": built, "running": _build_running(root)}


def create_studio_app(
    repo_dir: str | Path, *, spawn=None, provider: str = "anthropic"
) -> FastAPI:
    root = Path(repo_dir).resolve()
    app = FastAPI(title="autoproduct studio", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def same_origin_guard(request: Request, call_next):
        """Localhost is not a security boundary against the browser: a
        malicious page can form-POST to 127.0.0.1 (sweep finding). POSTs
        must come from the Studio itself."""
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
                from fastapi.responses import PlainTextResponse

                return PlainTextResponse("cross-origin POST rejected", status_code=403)
        return await call_next(request)

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
            features_dir = root / "product" / "features"
            feature_cards = ""
            if features_dir.is_dir():
                for d in sorted(features_dir.iterdir()):
                    state = (
                        "✅ 已完成" if (d / "REPORT.md").exists()
                        else ("待确认" if (d / "CONFIRMATION.md").exists() else "…")
                    )
                    feature_cards += f"<div class=card>{html.escape(d.name)} — {state}</div>"
            pending = _pending_feature(root)
            if pending:
                return _page(
                    "确认新功能 / Confirm the new feature",
                    f"<pre>{_md(pending / 'CONFIRMATION.md')}</pre>"
                    f"<form method=post action=/feature/build>"
                    f"<input type=hidden name=slug value='{html.escape(pending.name)}'>"
                    "<button>开始添加这个功能 / Build this feature</button></form>",
                )
            shots_dir = root / "product" / "screenshots"
            gallery = ""
            if shots_dir.is_dir():
                images = "".join(
                    f"<img src='/shots/{p.name}' style='max-width:100%;"
                    f"border:1px solid #ddd;border-radius:8px;margin:.4rem 0'>"
                    for p in sorted(shots_dir.glob("*.png"))
                )
                if images:
                    gallery = f"<h2>页面截图 / Screenshots</h2>{images}"
            acceptance = (
                "<p><a href='/acceptance'>📋 验收清单 / Acceptance walkthrough</a></p>"
                if (root / "product" / "ACCEPTANCE.md").exists()
                else ""
            )
            failed = _failed_tasks(root)
            retry_block = ""
            if failed:
                rows = "".join(
                    f"<form method=post action=/retry style='display:inline'>"
                    f"<input type=hidden name=task_id value='{html.escape(t)}'>"
                    f"<button class=secondary>重试 {html.escape(t)}</button></form> "
                    for t in failed
                )
                retry_block = (
                    f"<div class=card><b class=warn>没做成的模块 / Failed modules"
                    f"</b><p>可以先不管它们，产品其余部分能用；也可以单独重试：</p>{rows}</div>"
                )
            return _page(
                "你的产品 / Your product",
                f"<pre>{_md(report)}</pre>{acceptance}{gallery}{retry_block}"
                f"<h2>功能 / Features</h2>{feature_cards or '<p class=muted>(初版)</p>'}"
                "<h2>哪里不对？/ Something wrong?</h2>"
                "<p class=muted>用你自己的话说 — 小修会直接修好，需求变化会走正规变更。</p>"
                "<form method=post action=/correct>"
                "<textarea name=complaint style='min-height:80px' "
                "placeholder='例：下单按钮的文字应该是「参加接龙」，不是「提交」。'></textarea>"
                "<p><button>修正 / Correct it</button></p></form>"
                "<h2>添加新功能 / Add a feature</h2>"
                "<p class=muted>一次只写一个功能或改动 — 越小越准。One feature per "
                "FDR — smaller is better.</p>"
                "<form method=post action=/feature>"
                "<textarea name=fdr placeholder='例：住户可以取消自己的订单，取消后汇总自动更新。'></textarea>"
                "<p><button>检查这个功能 / Check this feature</button></p></form>"
                "<form method=post action=/undo style='margin-top:1.5rem'>"
                "<button class=secondary>⏪ 回到上一个版本 / Undo last change</button></form>",
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

    @app.get("/acceptance", response_class=HTMLResponse)
    def acceptance():
        return _page(
            "验收清单 / Acceptance walkthrough",
            f"<pre>{_md(root / 'product' / 'ACCEPTANCE.md')}</pre>"
            "<p><a href='/'>← 返回 / back</a></p>",
        )

    @app.get("/shots/{name}")
    def shot(name: str):
        from fastapi.responses import FileResponse

        path = (root / "product" / "screenshots" / name).resolve()
        if not path.is_file() or path.parent != (root / "product" / "screenshots").resolve():
            raise HTTPException(404)
        return FileResponse(path)

    @app.post("/correct")
    async def correct(request: Request):
        form = await request.form()
        complaint = str(form.get("complaint", "")).strip()
        if complaint:
            from autoproduct.upstream.correction import run_correction

            result = run_correction(root, complaint, provider=provider)
            (root / "product" / "CORRECTION-LOG.md").open("a", encoding="utf-8").write(
                f"- {result.status}: {complaint[:120]} → {result.detail}\n"
            )
        return RedirectResponse("/", status_code=303)

    @app.post("/retry")
    async def retry(request: Request):
        form = await request.form()
        task_id = str(form.get("task_id", ""))
        if task_id and not _build_running(root):
            proc = subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "autoproduct.cli", "retry-task", task_id,
                 "--repo-dir", str(root)],
                cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            (root / ".mas" / "build.pid").write_text(str(proc.pid), encoding="utf-8")
        return RedirectResponse("/", status_code=303)

    @app.post("/undo")
    def undo():
        from autoproduct.upstream.autopilot import undo_last

        undo_last(root)
        return RedirectResponse("/", status_code=303)

    @app.post("/feature")
    async def feature(request: Request):
        form = await request.form()
        fdr_text = str(form.get("fdr", "")).strip()
        if fdr_text:
            fdr_path = root / ".mas" / "pending-feature.md"
            fdr_path.write_text(fdr_text, encoding="utf-8")
            from autoproduct.upstream.autopilot import run_feature

            run_feature(root, fdr_path, provider=provider, yes=False)
        return RedirectResponse("/", status_code=303)

    @app.post("/feature/build")
    async def feature_build(request: Request):
        form = await request.form()
        slug = str(form.get("slug", ""))
        feature_dir = root / "product" / "features" / slug
        if feature_dir.is_dir() and not _build_running(root):
            fdr_path = feature_dir / "fdr.md"
            if spawn is not None:
                spawn(root)
            else:
                proc = subprocess.Popen(  # noqa: S603
                    [sys.executable, "-m", "autoproduct.cli", "add", str(fdr_path),
                     "--repo-dir", str(root), "--yes"],
                    cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                (root / ".mas" / "build.pid").write_text(str(proc.pid), encoding="utf-8")
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
