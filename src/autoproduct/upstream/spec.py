"""Spec stage (§13, built first per doc 14) — the anchor artifact.

generate → deterministic checks (ears_lint + coverage matrix) → critique
voters (Testability, Ambiguity) → bounded revision (fresh context, ≤2) →
Gate U3 (human approval). A spec that fails its deterministic checks after
revision is saved as `blocked`, never silently approved.

The spec is what `build` implements test-first and what the review stage
later verifies against — machine-checkable EARS criteria, a test skeleton
per criterion, and the domain profile's extras baked in.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autoproduct.providers import get_provider
from autoproduct.upstream import ears
from autoproduct.upstream.workspace import Project, load_project
from autoproduct.yamlx import extract_mapping

SPECWRITER_MARKER = "spec writer for a greenfield product system"
TESTABILITY_CRITIC_MARKER = "testability critic for product specs"
AMBIGUITY_CRITIC_MARKER = "ambiguity critic for product specs"

MAX_REVISIONS = 2


class TestSkeleton(BaseModel):
    path: str
    purpose: str
    covers: list[int] = Field(description="indices into criteria (0-based)")


class Spec(BaseModel):
    slug: str
    title: str
    status: str = "proposed"  # proposed | approved | blocked
    request: str
    profile: str
    design: str
    criteria: list[str]
    test_skeletons: list[TestSkeleton]
    lint_issues: list[dict] = Field(default_factory=list)
    critic_issues: list[dict] = Field(default_factory=list)
    revisions: int = 0


_WRITER_SYSTEM = f"""You are the {SPECWRITER_MARKER}. Produce a buildable
feature spec for the request, honoring the project constraints and profile
extras provided.

Rules:
- Acceptance criteria MUST use EARS syntax (The/When/While/If-then/Where
  ... shall ...) and measurable conditions — never vague words like
  "fast" or "user-friendly".
- Every criterion must be covered by at least one test skeleton (covers
  lists criterion indices, 0-based). Test paths live under tests/.
- The design section states the module layout and, where the profile
  demands it, API contracts / domains / permissions.
- Smallest spec that satisfies the request; no speculative features.

Respond with ONLY YAML:
title: ...
design: |
  ...
criteria:
  - "When ..., the system shall ..."
test_skeletons:
  - path: tests/test_x.py
    purpose: ...
    covers: [0, 1]
"""

_CRITIC_TEMPLATES = {
    "testability": (
        TESTABILITY_CRITIC_MARKER,
        "For each criterion: could a test objectively fail it? Flag criteria "
        "no test could falsify, and test skeletons whose purpose doesn't "
        "actually exercise the criteria they claim to cover.",
    ),
    "ambiguity": (
        AMBIGUITY_CRITIC_MARKER,
        "Flag criteria a second developer could implement differently while "
        "believing they complied: undefined terms, unstated limits, missing "
        "error behavior.",
    ),
}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "feature"


def _coverage_gaps(spec_data: dict) -> list[int]:
    covered = {
        i
        for skeleton in spec_data.get("test_skeletons", [])
        for i in skeleton.get("covers", [])
    }
    return [i for i in range(len(spec_data.get("criteria", []))) if i not in covered]


def _critique(provider, model: str, kind: str, spec_data: dict) -> list[dict]:
    marker, brief = _CRITIC_TEMPLATES[kind]
    system = (
        f"You are the {marker}. {brief}\n\nRespond with ONLY YAML:\n"
        "issues:\n  - severity: major|minor\n    anchor: criterion index or test path\n"
        "    problem: one sentence\n"
    )
    raw = provider.complete(
        model=model,
        system=system,
        user=yaml.safe_dump(
            {"criteria": spec_data.get("criteria", []),
             "test_skeletons": spec_data.get("test_skeletons", [])},
            sort_keys=False, allow_unicode=True,
        ),
        max_tokens=1024,
    )
    try:
        data = extract_mapping(raw, ("issues",))
    except ValueError:
        return []
    return [i for i in (data.get("issues") or []) if isinstance(i, dict)][:10]


def run_spec_stage(
    repo_dir: str | Path,
    request: str,
    *,
    provider: str = "anthropic",
    writer_model: str = "claude-opus-4-8",
    critic_model: str = "claude-sonnet-5",
) -> Spec:
    project: Project = load_project(repo_dir)
    provider_impl = get_provider(provider)
    profile = project.profile_data
    context = yaml.safe_dump(
        {
            "project": project.name,
            "profile": project.profile,
            "constraints": profile.get("constraints", []),
            "spec_extras": profile.get("spec_extras", []),
            "stack_hint": profile.get("stack_hint", ""),
        },
        sort_keys=False, allow_unicode=True,
    )

    feedback = ""
    spec_data: dict = {}
    lint: list = []
    critics: list[dict] = []
    for revision in range(MAX_REVISIONS + 1):
        raw = provider_impl.complete(
            model=writer_model,
            system=_WRITER_SYSTEM,
            user=f"<project>\n{context}</project>\n\n<request>\n{request}\n</request>"
            + (f"\n\n<revision_feedback>\n{feedback}\n</revision_feedback>" if feedback else ""),
            max_tokens=4096,
        )
        spec_data = extract_mapping(raw, ("criteria", "title"))
        lint = ears.lint_criteria([str(c) for c in spec_data.get("criteria", [])])
        gaps = _coverage_gaps(spec_data)
        critics = _critique(provider_impl, critic_model, "testability", spec_data)
        critics += _critique(provider_impl, critic_model, "ambiguity", spec_data)
        majors = [c for c in critics if c.get("severity") == "major"]
        if not lint and not gaps and not majors:
            break
        feedback = yaml.safe_dump(
            {
                "ears_lint": [i.model_dump() for i in lint],
                "uncovered_criteria_indices": gaps,
                "critic_majors": majors,
            },
            sort_keys=False, allow_unicode=True,
        )
    else:
        pass

    gaps = _coverage_gaps(spec_data)
    status = "proposed" if not lint and not gaps else "blocked"
    slug = _slugify(str(spec_data.get("title") or request))
    spec = Spec(
        slug=slug,
        title=str(spec_data.get("title", request))[:120],
        status=status,
        request=request,
        profile=project.profile,
        design=str(spec_data.get("design", "")),
        criteria=[str(c) for c in spec_data.get("criteria", [])],
        test_skeletons=[
            TestSkeleton.model_validate(s) for s in spec_data.get("test_skeletons", [])
        ],
        lint_issues=[i.model_dump() for i in lint],
        critic_issues=critics,
        revisions=revision,
    )
    _save(repo_dir, spec)
    return spec


def _spec_dir(repo_dir: str | Path, slug: str) -> Path:
    return Path(repo_dir) / "specs" / slug


def _save(repo_dir: str | Path, spec: Spec) -> None:
    directory = _spec_dir(repo_dir, spec.slug)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spec.yaml").write_text(
        yaml.safe_dump(spec.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    criteria = "\n".join(f"{i}. {c}" for i, c in enumerate(spec.criteria))
    skeletons = "\n".join(
        f"- `{s.path}` — {s.purpose} (covers {s.covers})" for s in spec.test_skeletons
    )
    (directory / "spec.md").write_text(
        f"# {spec.title}\n\nstatus: **{spec.status}** · profile: {spec.profile} · "
        f"revisions: {spec.revisions}\n\n## Design\n\n{spec.design}\n\n"
        f"## Acceptance criteria (EARS)\n\n{criteria}\n\n"
        f"## Test skeletons\n\n{skeletons}\n\n"
        f"Approve with: `autoproduct spec-approve {spec.slug}` (Gate U3)\n",
        encoding="utf-8",
    )


def load_spec(repo_dir: str | Path, slug: str) -> Spec:
    path = _spec_dir(repo_dir, slug) / "spec.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no spec {slug!r} under {repo_dir}/specs")
    return Spec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def approve_spec(repo_dir: str | Path, slug: str) -> Spec:
    """Gate U3 — the human acknowledgement that makes a spec buildable."""
    spec = load_spec(repo_dir, slug)
    if spec.status == "blocked":
        raise ValueError(
            f"spec {slug!r} is blocked by deterministic checks "
            f"(lint: {len(spec.lint_issues)}); fix and regenerate before approving"
        )
    spec.status = "approved"
    _save(repo_dir, spec)
    return spec
