"""The compounding loop (§09.8) — Stage 1: CLAUDE.md proposals only.

Aggregates the accumulated review record (final.yaml mirrors + per-voter
logs) into recurring-signal clusters, asks the Leader-class model to draft
constraint bullets, and writes a proposal. The proposal becomes a PR when
requested — and a human always merges it (ACE's reward-hacking lesson,
§08.2.2.7: self-updating context stays human-gated).
"""

from __future__ import annotations

import datetime
import re
from collections import Counter
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autoproduct.providers import get_provider
from autoproduct.yamlx import extract_mapping

SECTION_HEADER = "## Learned constraints (autoproduct)"
COMPOUND_MARKER = "compounding loop of a code review system"


class Signals(BaseModel):
    review_count: int = 0
    verdicts: dict[str, int] = Field(default_factory=dict)
    taxonomy_counts: dict[str, int] = Field(default_factory=dict)
    recurring_titles: list[tuple[str, int]] = Field(default_factory=list)
    voter_block_rates: dict[str, str] = Field(default_factory=dict)

    @property
    def has_material(self) -> bool:
        return bool(self.recurring_titles or self.taxonomy_counts)


class Proposal(BaseModel):
    constraint: str
    rationale: str


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def collect_signals(repo_dir: str | Path, days: int = 7) -> Signals:
    reviews_dir = Path(repo_dir) / ".mas" / "reviews"
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
    signals = Signals()
    titles: Counter[str] = Counter()
    title_display: dict[str, str] = {}

    for final_path in sorted(reviews_dir.glob("*/[0-9]*-final.yaml")):
        data = yaml.safe_load(final_path.read_text(encoding="utf-8")) or {}
        written = data.get("written_at")
        if written and datetime.datetime.fromisoformat(str(written)) < cutoff:
            continue
        signals.review_count += 1
        verdict = str(data.get("verdict"))
        signals.verdicts[verdict] = signals.verdicts.get(verdict, 0) + 1
        for finding in data.get("findings") or []:
            hint = finding.get("taxonomy_hint") or "untagged"
            signals.taxonomy_counts[hint] = signals.taxonomy_counts.get(hint, 0) + 1
            key = _normalize_title(finding.get("title", ""))
            if key:
                titles[key] += 1
                title_display.setdefault(key, finding.get("title", ""))

    signals.recurring_titles = [
        (title_display[key], count) for key, count in titles.most_common(10) if count >= 2
    ]

    voters_dir = Path(repo_dir) / ".mas" / "voters"
    if voters_dir.is_dir():
        for log_path in sorted(voters_dir.glob("*/log.yaml")):
            entries = yaml.safe_load(log_path.read_text(encoding="utf-8")) or []
            blocked = sum(1 for e in entries if e.get("status") != "OK")
            if entries:
                signals.voter_block_rates[log_path.parent.name] = (
                    f"{blocked}/{len(entries)} blocked"
                )
    return signals


_PROPOSER_SYSTEM = f"""You are the {COMPOUND_MARKER}. From the aggregated
review signals below, draft AT MOST 3 project constraints worth adding to
CLAUDE.md — rules that would have prevented the recurring findings.

Rules:
- Only propose constraints supported by signals appearing 2+ times.
- Each constraint is one imperative sentence a code author can follow.
- If the signals don't support any constraint, return an empty list.

Respond with ONLY YAML:
proposals:
  - constraint: ...
    rationale: one sentence citing the signal
"""


def propose(signals: Signals, *, provider: str, model: str) -> list[Proposal]:
    if not signals.has_material:
        return []
    user = yaml.safe_dump(
        {
            "reviews": signals.review_count,
            "verdicts": signals.verdicts,
            "taxonomy_counts": signals.taxonomy_counts,
            "recurring_findings": [
                {"title": t, "count": c} for t, c in signals.recurring_titles
            ],
        },
        sort_keys=False,
    )
    try:
        raw = get_provider(provider).complete(
            model=model, system=_PROPOSER_SYSTEM, user=user, max_tokens=1024
        )
        data = extract_mapping(raw, ("proposals",))
    except Exception:  # noqa: BLE001 — no proposal beats a bad proposal
        return []
    proposals = []
    for item in data.get("proposals") or []:
        try:
            proposals.append(Proposal.model_validate(item))
        except Exception:  # noqa: BLE001
            continue
    return proposals[:3]


def render_proposal(signals: Signals, proposals: list[Proposal], *, date: str) -> str:
    lines = [
        f"# Compounding-loop proposal — {date}",
        "",
        f"Window: {signals.review_count} review(s). "
        f"Verdicts: {signals.verdicts or '{}'}.",
        "",
        "## Recurring signals",
    ]
    for title, count in signals.recurring_titles or []:
        lines.append(f"- {count}× {title}")
    if not signals.recurring_titles:
        lines.append("- (none crossed the 2+ threshold)")
    lines += ["", "## Voter health"]
    for voter, rate in sorted(signals.voter_block_rates.items()):
        lines.append(f"- {voter}: {rate}")
    lines += ["", "## Proposed CLAUDE.md constraints"]
    for p in proposals:
        lines.append(f"- **{p.constraint}**  \n  _{p.rationale}_")
    if not proposals:
        lines.append("- (no constraint met the evidence bar this window)")
    lines += [
        "",
        "_Human-gated: these constraints take effect only if the CLAUDE.md "
        "change is merged (§09.8, ACE reward-hacking defense)._",
    ]
    return "\n".join(lines)


def apply_to_claude_md(repo_dir: str | Path, proposals: list[Proposal], *, date: str) -> Path:
    """Idempotently rewrite the autoproduct-owned section of CLAUDE.md;
    everything outside the section is never touched."""
    path = Path(repo_dir) / "CLAUDE.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(
        re.escape(SECTION_HEADER) + r".*?(?=\n## |\Z)", re.DOTALL
    )
    bullets = "\n".join(
        f"- {p.constraint} <!-- {date}: {p.rationale} -->" for p in proposals
    )
    section = f"{SECTION_HEADER}\n\n{bullets}\n"
    if pattern.search(existing):
        updated = pattern.sub(section, existing)
    else:
        updated = (existing.rstrip() + "\n\n" if existing.strip() else "") + section
    path.write_text(updated, encoding="utf-8")
    return path
