"""Greenfield workspace (`autoproduct init`) — Layer 4 for a NEW product.

A workspace is an ordinary git repo with:
- `.mas/project.yaml` — name + domain profile (doc 17: profiles are
  composable deltas, never forks)
- `CLAUDE.md` seeded with the profile's constraints — the same file every
  downstream voter and the compounding loop already use
- `specs/` — one directory per feature spec (the upstream anchor)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from pydantic import BaseModel

_PROFILES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "profiles"


class Project(BaseModel):
    name: str
    profile: str
    profile_data: dict


def available_profiles() -> list[str]:
    return sorted(p.stem for p in _PROFILES_DIR.glob("*.yaml"))


def load_profile(profile: str) -> dict:
    path = _PROFILES_DIR / f"{profile}.yaml"
    if not path.exists():
        raise ValueError(
            f"unknown profile {profile!r}; available: {available_profiles()}"
        )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def init_workspace(directory: str | Path, name: str, profile: str) -> Path:
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / ".mas" / "project.yaml").exists():
        raise FileExistsError(f"{root} is already an autoproduct workspace")
    profile_data = load_profile(profile)

    (root / ".mas").mkdir(exist_ok=True)
    (root / "specs").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / ".mas" / "project.yaml").write_text(
        yaml.safe_dump({"name": name, "profile": profile}, sort_keys=False),
        encoding="utf-8",
    )

    constraints = "\n".join(f"- {c}" for c in profile_data.get("constraints", []))
    (root / "CLAUDE.md").write_text(
        f"# {name} — project constraints\n\n"
        f"Domain profile: **{profile}** ({profile_data.get('description', '')}).\n"
        f"These constraints bind every spec and every implementation; the\n"
        f"review-stage Context voter enforces them as findings.\n\n"
        f"## Profile constraints\n\n{constraints}\n\n"
        f"## Stack\n\n{profile_data.get('stack_hint', '')}\n",
        encoding="utf-8",
    )
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            ".mas/\n__pycache__/\n.venv/\nnode_modules/\n"
            # API keys stay in the founder's environment, never in git.
            ".env\n.env.*\n"
        )

    _write_design_baseline(root, profile)

    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
             "commit", "-qm", f"autoproduct init: {name} ({profile} profile)"],
            cwd=root, check=True,
        )
    return root


_WEB_BASELINE_CSS = """/* autoproduct design baseline — include on every page:
   <link rel="stylesheet" href="/static/baseline.css"> */
:root{--brand:#2f6fed;--ink:#1c1e21;--muted:#6b7280;--bg:#f6f7f9;
--card:#fff;--radius:10px}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,'PingFang SC','Segoe UI',sans-serif;
color:var(--ink);background:var(--bg);line-height:1.6}
main,.container{max-width:720px;margin:0 auto;padding:1rem}
h1{font-size:1.5rem}h2{font-size:1.2rem}
.card{background:var(--card);border-radius:var(--radius);
padding:1rem 1.2rem;margin:.8rem 0;box-shadow:0 1px 3px rgba(0,0,0,.06)}
button,.btn{background:var(--brand);color:#fff;border:0;
border-radius:8px;padding:.65rem 1.4rem;font-size:1rem;cursor:pointer}
button.secondary{background:#e5e7eb;color:var(--ink)}
input,select,textarea{width:100%;padding:.6rem .8rem;border:1px solid #d1d5db;
border-radius:8px;font:inherit;margin:.3rem 0 .8rem}
label{font-weight:600;font-size:.92rem}
table{width:100%;border-collapse:collapse}
td,th{padding:.5rem;border-bottom:1px solid #eee;text-align:left}
.muted{color:var(--muted);font-size:.9rem}
@media(prefers-color-scheme:dark){:root{--ink:#e7e9ec;--bg:#111417;
--card:#1b1f24;--muted:#9aa3af}}
"""

_MINIPROGRAM_BASELINE_WXSS = """/* autoproduct 设计基线 — app.wxss 里 @import 使用；
   建议同时引入 WeUI (npm: weui-miniprogram) 获得微信原生观感 */
page{background:#f6f7f9;font-size:28rpx;color:#1c1e21;
font-family:-apple-system,'PingFang SC',sans-serif;line-height:1.6}
.card{background:#fff;border-radius:16rpx;padding:24rpx;margin:20rpx;
box-shadow:0 2rpx 8rpx rgba(0,0,0,.05)}
.btn-primary{background:#07c160;color:#fff;border-radius:12rpx;
padding:20rpx 0;text-align:center;font-size:32rpx}
.btn-secondary{background:#f2f2f2;color:#1c1e21}
.input{background:#fff;border:1rpx solid #e5e5e5;border-radius:12rpx;
padding:18rpx;margin:12rpx 0}
.muted{color:#888;font-size:24rpx}
"""


def _write_design_baseline(root: Path, profile: str) -> None:
    """M2: a design floor so built products look intentional, not
    engineer-styled. The implementer is constrained to use it."""
    if profile == "web":
        static = root / "static"
        static.mkdir(exist_ok=True)
        (static / "baseline.css").write_text(_WEB_BASELINE_CSS, encoding="utf-8")
        note = (
            "- Every HTML page links /static/baseline.css and uses its classes "
            "(.card, .btn, .container) — no inline style soup, no new CSS "
            "frameworks."
        )
    elif profile == "miniprogram":
        (root / "styles").mkdir(exist_ok=True)
        (root / "styles" / "baseline.wxss").write_text(
            _MINIPROGRAM_BASELINE_WXSS, encoding="utf-8"
        )
        note = (
            "- app.wxss @imports styles/baseline.wxss; pages use its classes "
            "(.card, .btn-primary) — WeUI (weui-miniprogram) preferred for "
            "complex components."
        )
    else:
        return
    claude = root / "CLAUDE.md"
    claude.write_text(
        claude.read_text(encoding="utf-8") + f"\n## Design baseline\n\n{note}\n",
        encoding="utf-8",
    )


def load_project(repo_dir: str | Path) -> Project:
    path = Path(repo_dir) / ".mas" / "project.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"{repo_dir} is not an autoproduct workspace (run `autoproduct init`)"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Project(
        name=data["name"],
        profile=data["profile"],
        profile_data=load_profile(data["profile"]),
    )
