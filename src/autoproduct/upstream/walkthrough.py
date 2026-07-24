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


_BATCH_SIZE = 8


def _render_batch(
    rows: list[tuple[str, str]],
    *,
    provider: str,
    model: str,
    language_sample: str,
) -> list[str] | None:
    """One LLM pass over a small batch; None unless EVERY step survived."""
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
    except Exception:  # noqa: BLE001
        return None
    lines = [line for line in rendered.splitlines() if "[ ]" in line]
    return lines if len(lines) >= len(rows) else None


def generate_walkthrough(
    repo_dir: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    language_sample: str = "",
) -> Path:
    """Per-BATCH deterministic floor (shakedown finding: a single pass over
    20+ criteria dropped steps and forced a wholesale fallback — batching
    keeps plain language for every batch that renders faithfully, raw EARS
    only for the batches that don't)."""
    root = Path(repo_dir).resolve()
    rows = built_criteria(root)
    lines: list[str] = []
    for start in range(0, len(rows), _BATCH_SIZE):
        batch = rows[start : start + _BATCH_SIZE]
        rendered = _render_batch(
            batch, provider=provider, model=model, language_sample=language_sample
        )
        if rendered is not None:
            lines += rendered[: len(batch) + 2]
        else:
            lines += [f"- [ ] ({title}) {criterion}" for title, criterion in batch]
    text = (
        "# 验收清单 / Acceptance walkthrough\n\n"
        "先运行 `autoproduct preview` 打开产品。\n\n" + "\n".join(lines)
        if rows
        else "# 验收清单 / Acceptance walkthrough\n\n(还没有已完成的功能 / nothing built yet)"
    )
    path = root / "product" / "ACCEPTANCE.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
