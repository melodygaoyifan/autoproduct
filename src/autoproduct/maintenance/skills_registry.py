"""Learned-skill registry (§09.12, ADR-006).

Recurring incident classes become skills: instructions injected into the
RootCause investigator's context when a new incident matches the class.
Creation is human-gated — after 3+ similar incidents the system DRAFTS a
skill with `status: proposed`; only skills a human flips to
`status: approved` are ever injected.

Matching is lexical (token overlap) in this iteration: no embedding
provider is configured, and a visible, debuggable matcher beats an
unavailable one. The FAISS upgrade (ADR-006) slots behind `match()`
without changing callers.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autoproduct.maintenance.correlate import _tokens
from autoproduct.providers import get_provider
from autoproduct.yamlx import extract_mapping

MATCH_MIN_OVERLAP = 3
RECURRENCE_THRESHOLD = 3

SKILL_DRAFT_MARKER = "distilling a learned skill for a maintenance system"

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)

_DRAFT_SYSTEM = f"""You are {SKILL_DRAFT_MARKER}. The incidents below are
the same recurring class. Write a short skill an investigator can apply
next time: what this class looks like, where the cause usually lives, and
the fastest diagnostic step.

Respond with ONLY YAML:
name: short-kebab-case-slug
description: one line
body: |
  2-6 lines of investigator guidance
"""


class LearnedSkill(BaseModel):
    name: str
    description: str = ""
    status: str = "proposed"  # proposed | approved
    trigger_tokens: list[str] = Field(default_factory=list)
    instances: list[str] = Field(default_factory=list)
    body: str = ""
    path: str = ""


def _registry_dir(repo_dir: str | Path) -> Path:
    return Path(repo_dir) / ".mas" / "learned-skills"


def load_registry(repo_dir: str | Path) -> list[LearnedSkill]:
    skills = []
    for path in sorted(_registry_dir(repo_dir).glob("*.md")):
        match = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
        if not match:
            continue
        try:
            meta = yaml.safe_load(match.group(1)) or {}
            skills.append(
                LearnedSkill(**meta, body=match.group(2).strip(), path=str(path))
            )
        except Exception:  # noqa: BLE001 — a malformed skill never blocks triage
            continue
    return skills


COSINE_MIN = 0.30


def _tfidf_cosine(query: set[str], docs: list[set[str]], target: int) -> float:
    """TF-IDF cosine between the query and docs[target], idf over all docs
    plus the query. Pure python — a real matcher upgrade over raw overlap;
    the embedding/FAISS tier (ADR-006) slots in when an embedding provider
    is configured."""
    import math

    corpus = docs + [query]
    n_docs = len(corpus)

    def idf(token: str) -> float:
        containing = sum(1 for d in corpus if token in d)
        return math.log((1 + n_docs) / (1 + containing)) + 1

    def vector(doc: set[str]) -> dict[str, float]:
        return {t: idf(t) for t in doc}

    a, b = vector(query), vector(docs[target])
    dot = sum(w * b[t] for t, w in a.items() if t in b)
    norm = math.sqrt(sum(w * w for w in a.values())) * math.sqrt(
        sum(w * w for w in b.values())
    )
    return dot / norm if norm else 0.0


def match(incident_text: str, skills: list[LearnedSkill]) -> LearnedSkill | None:
    """Best APPROVED skill by TF-IDF cosine over trigger tokens, with the
    raw-overlap floor as a guard against degenerate short texts."""
    query = _tokens(incident_text)
    approved = [s for s in skills if s.status == "approved"]
    if not approved:
        return None
    docs = [set(s.trigger_tokens) for s in approved]
    best, best_score = None, 0.0
    for i, skill in enumerate(approved):
        overlap = len(query & docs[i])
        if overlap < MATCH_MIN_OVERLAP:
            continue
        score = _tfidf_cosine(query, docs, i)
        if score >= COSINE_MIN and score > best_score:
            best, best_score = skill, score
    return best


def _history_path(repo_dir: str | Path) -> Path:
    return _registry_dir(repo_dir) / "history.yaml"


def record_incident(repo_dir: str | Path, incident_id: str, incident_text: str) -> list[dict]:
    """Append this incident's signature; return prior similar entries."""
    path = _history_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    history = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    history = history or []
    tokens = sorted(_tokens(incident_text))
    similar = [
        e for e in history
        if len(set(e.get("tokens", [])) & set(tokens)) >= MATCH_MIN_OVERLAP
        and e["incident_id"] != incident_id
    ]
    if not any(e["incident_id"] == incident_id for e in history):
        history.append(
            {"incident_id": incident_id, "tokens": tokens, "text": incident_text[:500]}
        )
        # Bounded (PR #12 self-review): recurrence detection only needs a
        # recent window; skills carry the long-term memory.
        history = history[-500:]
        path.write_text(yaml.safe_dump(history, sort_keys=False), encoding="utf-8")
    return similar


def maybe_draft_skill(
    repo_dir: str | Path,
    incident_texts: list[str],
    *,
    provider: str,
    model: str,
) -> LearnedSkill | None:
    """3+ similar incidents and no covering skill → draft one (proposed)."""
    combined = "\n---\n".join(incident_texts)
    existing = load_registry(repo_dir)
    tokens = _tokens(combined)
    for skill in existing:  # proposed or approved both count as covering
        if len(tokens & set(skill.trigger_tokens)) >= MATCH_MIN_OVERLAP:
            return None
    try:
        raw = get_provider(provider).complete(
            model=model,
            system=_DRAFT_SYSTEM,
            user=f"<incidents>\n{combined}\n</incidents>",
            max_tokens=1024,
        )
        data = extract_mapping(raw, ("name", "body"))
    except Exception:  # noqa: BLE001 — no draft beats a bad draft
        return None
    name = re.sub(r"[^a-z0-9-]", "-", str(data.get("name", "skill")).lower())[:60]
    skill = LearnedSkill(
        name=name,
        description=str(data.get("description", "")),
        status="proposed",
        trigger_tokens=sorted(tokens)[:40],
        body=str(data.get("body", "")).strip(),
    )
    path = _registry_dir(repo_dir) / f"{name}.md"
    frontmatter = yaml.safe_dump(
        skill.model_dump(exclude={"body", "path"}), sort_keys=False
    )
    path.write_text(f"---\n{frontmatter}---\n\n{skill.body}\n", encoding="utf-8")
    skill.path = str(path)
    return skill
