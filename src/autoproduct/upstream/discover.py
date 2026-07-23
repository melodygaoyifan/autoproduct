"""Discovery stage (§13.26) — the ProductBrief and the hypothesis ledger.

Every claim in a brief is a hypothesis tagged with an evidence class:
`measured` (you have data), `sourced` (someone credible published it), or
`assumed` (be honest). Untagged claims are a deterministic failure — the
charter's no-fabricated-user-evidence rule (§13.26.7) enforced at the
schema level. Talking to real users stays human work; the ledger is what
the Maintenance stage later reconciles against production telemetry.

Gate U1 (`brief-approve`) is the human problem-selection decision — the
system prepares options, never chooses (§README scope).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from autoproduct.providers import get_provider
from autoproduct.upstream.workspace import load_project
from autoproduct.yamlx import extract_mapping

BRIEFWRITER_MARKER = "product brief writer in a greenfield discovery stage"
BRIEF_CRITIC_MARKER = "brief critic panel"

EVIDENCE_CLASSES = {"measured", "sourced", "assumed"}
MAX_REVISIONS = 1


class Hypothesis(BaseModel):
    statement: str
    evidence: str

    @field_validator("evidence")
    @classmethod
    def _known_class(cls, value: str) -> str:
        if value not in EVIDENCE_CLASSES:
            raise ValueError(f"evidence must be one of {sorted(EVIDENCE_CLASSES)}")
        return value


class Brief(BaseModel):
    title: str
    status: str = "proposed"  # proposed | approved | blocked
    problem: str
    target_user: str
    hypotheses: list[Hypothesis]
    scope_now: list[str] = Field(min_length=1)
    scope_later: list[str] = Field(default_factory=list)
    scope_never: list[str] = Field(default_factory=list)
    success_metrics: list[str] = Field(min_length=1)
    critic_issues: list[dict] = Field(default_factory=list)
    revisions: int = 0


_WRITER_SYSTEM = f"""You are the {BRIEFWRITER_MARKER}. Turn the idea into a
one-page ProductBrief the human can approve or reject.

Rules:
- Every hypothesis carries an evidence class: measured | sourced | assumed.
  Never fabricate user evidence — when in doubt, tag it `assumed`.
- scope_now is the smallest product worth shipping; be aggressive about
  pushing items to scope_later / scope_never.
- success_metrics are measurable (a number and a direction), not vibes.

Respond with ONLY YAML:
title: ...
problem: ...
target_user: ...
hypotheses:
  - statement: ...
    evidence: measured|sourced|assumed
scope_now: [...]
scope_later: [...]
scope_never: [...]
success_metrics: [...]
"""

_CRITIC_SYSTEM = f"""You are the {BRIEF_CRITIC_MARKER}: judge the brief from
four angles — desirability (would the target user care), feasibility (can a
small team build scope_now), viability (does the metric imply a working
product), scope discipline (is scope_now really minimal). Flag majors only
where the brief would mislead the human decision at Gate U1.

Respond with ONLY YAML:
issues:
  - severity: major|minor
    lens: desirability|feasibility|viability|scope
    problem: one sentence
"""


def run_discovery(
    repo_dir: str | Path,
    idea: str,
    *,
    provider: str = "anthropic",
    writer_model: str = "claude-opus-4-8",
    critic_model: str = "claude-sonnet-5",
) -> Brief:
    project = load_project(repo_dir)
    provider_impl = get_provider(provider)
    context = yaml.safe_dump(
        {"project": project.name, "profile": project.profile,
         "constraints": project.profile_data.get("constraints", [])},
        sort_keys=False, allow_unicode=True,
    )

    feedback = ""
    brief: Brief | None = None
    critics: list[dict] = []
    for revision in range(MAX_REVISIONS + 1):
        raw = provider_impl.complete(
            model=writer_model,
            system=_WRITER_SYSTEM,
            user=f"<project>\n{context}</project>\n\n<idea>\n{idea}\n</idea>"
            + (f"\n\n<revision_feedback>\n{feedback}\n</revision_feedback>" if feedback else ""),
            max_tokens=4096,
        )
        try:
            data = extract_mapping(raw, ("hypotheses", "title"))
        except ValueError:
            # Non-parsing output (common with non-English content: unquoted
            # colons break YAML) is revision feedback, not a crash.
            feedback = (
                "Your previous response was not a parseable YAML mapping. "
                "Respond with ONLY the YAML schema given, and double-quote "
                "every string value."
            )
            brief = None
            continue
        try:
            brief = Brief(
                title=str(data.get("title", idea))[:120],
                problem=str(data.get("problem", "")),
                target_user=str(data.get("target_user", "")),
                hypotheses=[Hypothesis.model_validate(h) for h in data.get("hypotheses", [])],
                scope_now=[str(s) for s in data.get("scope_now", [])],
                scope_later=[str(s) for s in data.get("scope_later", [])],
                scope_never=[str(s) for s in data.get("scope_never", [])],
                success_metrics=[str(m) for m in data.get("success_metrics", [])],
                revisions=revision,
            )
        except Exception as exc:  # noqa: BLE001 — schema failure feeds revision
            feedback = f"schema violation: {exc}"
            brief = None
            continue
        raw_critique = provider_impl.complete(
            model=critic_model,
            system=_CRITIC_SYSTEM,
            user=yaml.safe_dump(brief.model_dump(exclude={"critic_issues"}), sort_keys=False, allow_unicode=True),
            max_tokens=1024,
        )
        try:
            critics = [
                i for i in (extract_mapping(raw_critique, ("issues",)).get("issues") or [])
                if isinstance(i, dict)
            ][:10]
        except ValueError:
            critics = []
        majors = [c for c in critics if c.get("severity") == "major"]
        if not majors:
            break
        feedback = yaml.safe_dump({"critic_majors": majors}, sort_keys=False, allow_unicode=True)

    if brief is None:
        raise ValueError(f"brief failed schema after {MAX_REVISIONS + 1} attempts: {feedback}")
    brief.critic_issues = critics
    _save(repo_dir, brief)
    _append_ledger(repo_dir, brief)
    return brief


def _save(repo_dir: str | Path, brief: Brief) -> None:
    directory = Path(repo_dir) / "product"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "brief.yaml").write_text(
        yaml.safe_dump(brief.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    hypotheses = "\n".join(
        f"- ({h.evidence}) {h.statement}" for h in brief.hypotheses
    )
    (directory / "brief.md").write_text(
        f"# {brief.title}\n\nstatus: **{brief.status}**\n\n## Problem\n\n"
        f"{brief.problem}\n\n## Target user\n\n{brief.target_user}\n\n"
        f"## Hypotheses (evidence-tagged)\n\n{hypotheses}\n\n"
        f"## Scope now\n\n" + "\n".join(f"- {s}" for s in brief.scope_now)
        + "\n\n## Later / Never\n\n"
        + "\n".join(f"- later: {s}" for s in brief.scope_later)
        + "\n" + "\n".join(f"- never: {s}" for s in brief.scope_never)
        + "\n\n## Success metrics\n\n"
        + "\n".join(f"- {m}" for m in brief.success_metrics)
        + f"\n\nApprove with: `autoproduct brief-approve` (Gate U1)\n",
        encoding="utf-8",
    )


def _append_ledger(repo_dir: str | Path, brief: Brief) -> None:
    """The hypothesis ledger — what Maintenance reconciles after launch."""
    path = Path(repo_dir) / ".mas" / "hypotheses.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    existing = existing or []
    known = {e["statement"] for e in existing}
    for h in brief.hypotheses:
        if h.statement not in known:
            existing.append(
                {"statement": h.statement, "evidence": h.evidence, "verified": None}
            )
    path.write_text(yaml.safe_dump(existing, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_brief(repo_dir: str | Path) -> Brief:
    path = Path(repo_dir) / "product" / "brief.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no brief under {repo_dir}/product (run `autoproduct discover`)")
    return Brief.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def approve_brief(repo_dir: str | Path) -> Brief:
    """Gate U1 — the human problem-selection decision."""
    brief = load_brief(repo_dir)
    brief.status = "approved"
    _save(repo_dir, brief)
    return brief
