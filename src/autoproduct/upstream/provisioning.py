"""Service provisioning — real persistence for built products.

The demo-to-production gap: generated apps used in-memory stores because
nothing provisioned a database. v1 policy, honest about what it can do:

- **local tier (automatic)**: a SQLite database is provisioned in the
  workspace (`data/app.db`), registered in `.mas/services.yaml`, and
  advertised to the implementer — generated code persists for real with
  zero accounts and zero network.
- **cloud tier (guided)**: per-profile catalog (Supabase/Postgres for web,
  微信云开发 for 小程序) — the system writes `SERVICES.md` setup steps in
  plain language and a secrets TEMPLATE. Credentials go in
  `.mas/secrets.yaml` (never committed — `.mas/` is gitignored; values are
  never echoed into prompts, reports, or generated code: code reads env).

The implementer sees service NAMES and env-var names, never values.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CLOUD_CATALOG = {
    "web": {
        "name": "Supabase / Postgres",
        "env": ["DATABASE_URL"],
        "steps": [
            "注册 supabase.com（免费档即可），新建项目 / Create a free project at supabase.com",
            "复制 Project Settings → Database → Connection string (URI)",
            "把它填进 .mas/secrets.yaml 的 DATABASE_URL / Put it in .mas/secrets.yaml as DATABASE_URL",
            "重新运行 autoproduct preview — 应用会改用云数据库 / Re-run preview to use the cloud DB",
        ],
    },
    "miniprogram": {
        "name": "微信云开发 (WeChat Cloud Base)",
        "env": ["WX_CLOUD_ENV"],
        "steps": [
            "微信开发者工具 → 云开发 → 开通（按量付费有免费额度）",
            "创建环境，复制环境 ID / Create an environment, copy its ID",
            "填进 .mas/secrets.yaml 的 WX_CLOUD_ENV",
            "小程序代码通过 wx.cloud.init({env: ...}) 使用云数据库/云函数",
        ],
    },
    "app": {
        "name": "Supabase (auth + database)",
        "env": ["SUPABASE_URL", "SUPABASE_ANON_KEY"],
        "steps": [
            "Create a free project at supabase.com",
            "Copy Project URL and anon key into .mas/secrets.yaml",
        ],
    },
}


def provision_local(repo_dir: str | Path) -> dict:
    """Automatic tier: real SQLite persistence, zero accounts."""
    root = Path(repo_dir).resolve()
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)
    keep = data_dir / ".gitkeep"
    keep.touch()
    gitignore = root / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if "data/*.db" not in text:
        gitignore.write_text(text + "data/*.db\n", encoding="utf-8")

    services_path = root / ".mas" / "services.yaml"
    services_path.parent.mkdir(exist_ok=True)
    services = (
        yaml.safe_load(services_path.read_text(encoding="utf-8"))
        if services_path.exists()
        else {}
    ) or {}
    services["database"] = {
        "kind": "sqlite",
        "env": "DATABASE_PATH",
        "value_hint": "data/app.db",  # a path, not a secret
    }
    services_path.write_text(yaml.safe_dump(services, sort_keys=False), encoding="utf-8")
    return services


def write_cloud_guide(repo_dir: str | Path, profile: str) -> Path | None:
    root = Path(repo_dir).resolve()
    catalog = CLOUD_CATALOG.get(profile)
    if not catalog:
        return None
    steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(catalog["steps"]))
    env_lines = "\n".join(f"{name}: \"填这里 / paste here\"" for name in catalog["env"])
    guide = root / "SERVICES.md"
    guide.write_text(
        f"# 接入云服务 / Cloud services (可选 optional)\n\n"
        f"你的产品现在用本地数据库（data/app.db），功能是完整的。\n"
        f"想让数据在多台设备/多个用户之间共享时，接入云服务：\n"
        f"Your product currently uses a local database — fully functional.\n"
        f"Connect a cloud service when data must be shared across devices/users.\n\n"
        f"## {catalog['name']}\n\n{steps}\n\n"
        f"凭据放这里（此文件永不提交、永不出现在报告里）：\n"
        f"Credentials go here (never committed, never shown in reports):\n\n"
        f"`.mas/secrets.yaml`:\n```yaml\n{env_lines}\n```\n",
        encoding="utf-8",
    )
    secrets_path = root / ".mas" / "secrets.yaml"
    if not secrets_path.exists():
        secrets_path.write_text(
            "# credentials — this file is never committed (.mas/ is gitignored)\n",
            encoding="utf-8",
        )
    return guide


def services_context(repo_dir: str | Path) -> str:
    """What the implementer is told: service names and env-var names only —
    NEVER values."""
    services_path = Path(repo_dir) / ".mas" / "services.yaml"
    if not services_path.exists():
        return ""
    services = yaml.safe_load(services_path.read_text(encoding="utf-8")) or {}
    if not services:
        return ""
    lines = []
    for name, cfg in services.items():
        if cfg.get("kind") == "sqlite":
            lines.append(
                f"- {name}: SQLite file at the path in env {cfg['env']} "
                f"(default {cfg['value_hint']}). USE IT for persistence — "
                "the stdlib sqlite3 module; never an in-memory store for "
                "data that must survive restarts."
            )
        else:
            lines.append(f"- {name}: configured via env {cfg.get('env')}")
    return "Available services (read config from env, never hardcode):\n" + "\n".join(lines)


def auto_provision_cloud(repo_dir: str | Path, profile: str) -> dict:
    """Cloud AUTO-provisioning driver — gated on the platform CLI being
    installed and authenticated (Supabase for web today). Absent tooling
    degrades to the guided SERVICES.md path, visibly. 微信云开发 has no
    public provisioning CLI: guided-only by platform constraint."""
    import json
    import shutil
    import subprocess

    root = Path(repo_dir).resolve()
    if profile != "web":
        return {"status": "guided_only",
                "detail": "此平台没有公开的自动开通接口，请按 SERVICES.md 手动开通"}
    if not shutil.which("supabase"):
        return {"status": "unavailable",
                "detail": "supabase CLI not installed (brew install supabase/tap/supabase) "
                "— guided path in SERVICES.md still works"}
    projects = subprocess.run(
        ["supabase", "projects", "list", "--output", "json"],
        capture_output=True, text=True, timeout=60,
    )
    if projects.returncode != 0:
        return {"status": "unavailable",
                "detail": "supabase CLI not logged in (supabase login)"}
    created = subprocess.run(
        ["supabase", "projects", "create", root.name, "--output", "json"],
        capture_output=True, text=True, timeout=300,
    )
    if created.returncode != 0:
        return {"status": "error",
                "detail": (created.stderr or created.stdout)[-200:]}
    info = json.loads(created.stdout or "{}")
    secrets_path = root / ".mas" / "secrets.yaml"
    secrets = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) if secrets_path.exists() else {}
    secrets = secrets or {}
    if info.get("database", {}).get("host"):
        secrets["DATABASE_URL"] = (
            f"postgresql://postgres@{info['database']['host']}:5432/postgres"
        )
    secrets_path.write_text(yaml.safe_dump(secrets, sort_keys=False), encoding="utf-8")
    return {"status": "provisioned", "detail": f"supabase project {info.get('id', root.name)}; "
            "finish the connection string password in .mas/secrets.yaml"}


def preview_env(repo_dir: str | Path) -> dict[str, str]:
    """Env injected when running the product: local paths + any secrets."""
    root = Path(repo_dir).resolve()
    env: dict[str, str] = {}
    services_path = root / ".mas" / "services.yaml"
    if services_path.exists():
        for cfg in (yaml.safe_load(services_path.read_text(encoding="utf-8")) or {}).values():
            if cfg.get("kind") == "sqlite":
                env[cfg["env"]] = str(root / cfg["value_hint"])
    secrets_path = root / ".mas" / "secrets.yaml"
    if secrets_path.exists():
        secrets = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
        env.update({k: str(v) for k, v in secrets.items() if v and "填这里" not in str(v)})
    return env
