"""M2 — screenshots: founders validate by LOOKING, not reading file lists.

Availability-gated like every external tool: Playwright (web) and
miniprogram devtools are used when installed; their absence is a visible
note, never a silent skip. Captures land in product/screenshots/ and are
surfaced by the Studio and the build report.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel


class ShotResult(BaseModel):
    captured: list[str] = []
    note: str = ""


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def capture_web(workspace: str | Path, paths: list[str] | None = None, port: int = 8642) -> ShotResult:
    root = Path(workspace).resolve()
    if not _playwright_available():
        return ShotResult(
            note="screenshots skipped: playwright not installed "
            "(`uv add playwright && uv run playwright install chromium`)"
        )
    entry = next(
        (e for e in ("app/main.py", "main.py", "app.py") if (root / e).exists()), None
    )
    if not entry:
        return ShotResult(note="screenshots skipped: no runnable web entry")

    import os
    import socket

    from autoproduct.upstream.provisioning import preview_env

    out_dir = root / "product" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, str(root / entry)],
        cwd=root,
        env={**os.environ, "PORT": str(port), **preview_env(root)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    captured: list[str] = []
    try:
        for _ in range(40):
            try:
                socket.create_connection(("127.0.0.1", port), 1).close()
                break
            except OSError:
                time.sleep(0.5)
        else:
            return ShotResult(note="screenshots skipped: server never listened")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 900, "height": 700})
            for url_path in paths or ["/"]:
                slug = url_path.strip("/").replace("/", "-") or "home"
                page.goto(f"http://127.0.0.1:{port}{url_path}", timeout=15000)
                target = out_dir / f"{slug}.png"
                page.screenshot(path=str(target), full_page=True)
                captured.append(str(target.relative_to(root)))
            browser.close()
        return ShotResult(captured=captured)
    except Exception as exc:  # noqa: BLE001 — capture is best-effort, visibly
        return ShotResult(captured=captured, note=f"screenshot error: {exc}")
    finally:
        server.terminate()


def capture(workspace: str | Path, profile: str) -> ShotResult:
    if profile == "web":
        return capture_web(workspace)
    if profile == "miniprogram":
        return ShotResult(
            note="小程序截图：用微信开发者工具打开项目即可预览各页面 "
            "(devtools-cli screenshots land here when configured)"
        )
    return ShotResult(note=f"screenshots not supported for profile {profile!r} yet")
