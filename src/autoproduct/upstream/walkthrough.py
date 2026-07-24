"""M4 — 验收清单: the founder's trust instrument.

EARS criteria become a checklist a non-technical person runs in five
minutes: open the preview, do these things, expect these results.
Deterministic core (every built criterion becomes a checkbox — nothing
can be silently omitted); an LLM pass renders it in the founder's
language, degrading to the raw criteria if it fails.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from autoproduct.providers import get_provider

WALKTHROUGH_MARKER = "acceptance walkthrough writer for non-technical founders"

_SYSTEM = f"""You are the {WALKTHROUGH_MARKER}. Turn each acceptance
criterion below into ONE checklist step a non-technical founder can
perform against the running product: an action in plain words + what they
should see. SAME LANGUAGE as the sample. Keep every criterion — one step
each, numbered, markdown checkboxes. Start with how to open the preview
(`autoproduct preview`). Respond with the markdown only."""


def built_criteria(repo_dir: str | Path) -> list[tuple[str, str]]:
    """(spec title, criterion) for every BUILT spec — the deterministic
    floor: a criterion cannot be silently left out of the checklist."""
    rows = []
    for spec_file in sorted(Path(repo_dir).glob("specs/*/spec.yaml")):
        data = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        if data.get("built"):
            for criterion in data.get("criteria", []):
                rows.append((str(data.get("title", "")), str(criterion)))
    return rows


def generate_walkthrough(
    repo_dir: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    language_sample: str = "",
) -> Path:
    root = Path(repo_dir).resolve()
    rows = built_criteria(root)
    fallback = "# 验收清单 / Acceptance walkthrough\n\n先运行 `autoproduct preview` 打开产品。\n\n" + "\n".join(
        f"- [ ] ({title}) {criterion}" for title, criterion in rows
    )
    text = fallback
    if rows:
        try:
            rendered = get_provider(provider).complete(
                model=model,
                system=_SYSTEM,
                user=yaml.safe_dump(
                    [{"feature": t, "criterion": c} for t, c in rows],
                    sort_keys=False, allow_unicode=True,
                )
                + f"\n<language_sample>\n{language_sample[:300]}\n</language_sample>",
                max_tokens=2048,
            )
            # Deterministic floor: keep the LLM version only if it kept
            # every step.
            if rendered.count("[ ]") >= len(rows):
                text = rendered
        except Exception:  # noqa: BLE001 — fallback is always valid
            pass
    path = root / "product" / "ACCEPTANCE.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
