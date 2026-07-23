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
        gitignore.write_text(".mas/\n__pycache__/\n.venv/\nnode_modules/\n")

    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
             "commit", "-qm", f"autoproduct init: {name} ({profile} profile)"],
            cwd=root, check=True,
        )
    return root


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
