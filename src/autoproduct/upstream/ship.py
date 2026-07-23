"""`autoproduct ship` — deployment artifacts + founder-language guide.

v1 posture: generate everything needed to deploy (Dockerfile, platform
config, DEPLOY.md in plain language) and, where a platform CLI is
installed and authenticated, say exactly the one command to run — the
system never deploys to production autonomously (§08.1.8 hard ceiling;
here that ceiling and the founder's interests coincide: the account, the
billing, and the button are theirs).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from autoproduct.upstream.workspace import load_project

_DOCKERFILE = """FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt || true
ENV PORT=8080 DATABASE_PATH=/data/app.db
VOLUME /data
EXPOSE 8080
CMD ["python", "{entry}"]
"""


def _web_entry(root: Path) -> str | None:
    for entry in ("app/main.py", "main.py", "app.py"):
        if (root / entry).exists():
            return entry
    return None


def ship(repo_dir: str | Path) -> Path:
    root = Path(repo_dir).resolve()
    project = load_project(root)
    if project.profile == "miniprogram":
        return _ship_miniprogram(root)
    return _ship_web(root)


def _ship_web(root: Path) -> Path:
    entry = _web_entry(root) or "app/main.py"
    (root / "Dockerfile").write_text(_DOCKERFILE.format(entry=entry), encoding="utf-8")
    if not (root / "requirements.txt").exists():
        (root / "requirements.txt").write_text("# stdlib-only by default\n", encoding="utf-8")

    railway = shutil.which("railway") is not None
    fly = shutil.which("flyctl") is not None
    one_command = (
        "```\nrailway init && railway up\n```\n（railway CLI 已安装，登录后运行即可）"
        if railway
        else ("```\nflyctl launch\n```" if fly else "")
    )
    guide = root / "DEPLOY.md"
    guide.write_text(
        "# 上线你的产品 / Deploy your product\n\n"
        "已生成 `Dockerfile` — 任何能跑容器的平台都能部署。\n"
        "A `Dockerfile` was generated — any container platform can run this.\n\n"
        "## 最简单的路径 / Easiest path\n\n"
        + (one_command or
           "1. 注册 railway.app 或 fly.io（都有免费档）\n"
           "2. 安装它们的命令行工具并登录\n"
           "3. 在这个目录运行 `railway up` 或 `flyctl launch`\n")
        + "\n\n## 注意 / Notes\n\n"
        "- 数据库文件在 `/data`（容器卷）— 平台的持久卷要挂到 `/data`。\n"
        "- 云数据库凭据用平台的环境变量设置（见 SERVICES.md），不要写进代码。\n"
        "- 上线前建议再跑一次 `autoproduct review HEAD~1` 看最后一次改动。\n",
        encoding="utf-8",
    )
    return guide


def _ship_miniprogram(root: Path) -> Path:
    config = root / "project.config.json"
    if not config.exists():
        config.write_text(
            '{\n  "appid": "填你的小程序 AppID / your miniprogram AppID",\n'
            '  "projectname": "%s",\n  "compileType": "miniprogram"\n}\n' % root.name,
            encoding="utf-8",
        )
    guide = root / "DEPLOY.md"
    guide.write_text(
        "# 发布你的小程序 / Publish your 小程序\n\n"
        "1. 在 mp.weixin.qq.com 注册小程序，拿到 AppID，填进 `project.config.json`。\n"
        "2. 用微信开发者工具打开这个目录（导入项目）。\n"
        "3. 工具里点「预览」→ 手机扫码试用；点「上传」→ 生成体验版。\n"
        "4. 在小程序管理后台把体验版提交审核；类目要和内容一致（见 CLAUDE.md 的边界）。\n"
        "5. 审核通过后发布正式版。\n\n"
        "自动化上传（可选，团队用）：`npm i miniprogram-ci`，在管理后台生成上传密钥，\n"
        "然后 `miniprogram-ci upload --pp ./ --appid <AppID> --pkp <密钥路径>`。\n",
        encoding="utf-8",
    )
    return guide
