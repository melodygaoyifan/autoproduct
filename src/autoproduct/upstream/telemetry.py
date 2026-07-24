"""M5 — built-in telemetry: without it, success metrics are fiction.

Every provisioned product gets a zero-dependency event tracker (JSONL on
disk; privacy-clean, self-hosted). The implementer is constrained to
track the actions the success metrics name. `autoproduct digest` turns
the recorded events into a plain-language weekly digest and reconciles
the hypothesis ledger — the loop Discovery opened, finally closed with
observed reality instead of vibes.
"""

from __future__ import annotations

import datetime
import json
from collections import Counter
from pathlib import Path

import yaml

from autoproduct.providers import get_provider

DIGEST_MARKER = "weekly digest writer for non-technical founders"

_TELEMETRY_PY = '''"""autoproduct telemetry — zero-dependency event tracking.

from telemetry import track
track("order_created", {"quantity": 2})

Events append to data/events.jsonl (local, private). The weekly digest
(`autoproduct digest`) reads them; nothing leaves the machine.
"""
import json
import os
import time


def track(event, props=None):
    path = os.environ.get("EVENTS_PATH", "data/events.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(
            {"event": event, "ts": time.time(), "props": props or {}},
            ensure_ascii=False) + "\\n")
'''

_TELEMETRY_JS = """// autoproduct telemetry (小程序) — 本地事件记录，不出设备
// const { track } = require('../../utils/telemetry')
// track('order_created', { quantity: 2 })
function track(event, props) {
  try {
    const key = 'ap_events'
    const events = wx.getStorageSync(key) || []
    events.push({ event, ts: Date.now(), props: props || {} })
    wx.setStorageSync(key, events.slice(-2000))
  } catch (e) { /* telemetry never breaks the product */ }
}
module.exports = { track }
"""


def install_telemetry(repo_dir: str | Path, profile: str) -> Path:
    root = Path(repo_dir).resolve()
    if profile == "miniprogram":
        target = root / "utils" / "telemetry.js"
        target.parent.mkdir(exist_ok=True)
        target.write_text(_TELEMETRY_JS, encoding="utf-8")
    else:
        target = root / "telemetry.py"
        target.write_text(_TELEMETRY_PY, encoding="utf-8")
    claude = root / "CLAUDE.md"
    if claude.exists() and "telemetry" not in claude.read_text(encoding="utf-8"):
        claude.write_text(
            claude.read_text(encoding="utf-8")
            + "\n## Telemetry\n\n- Track every user action named in the success "
            "metrics via the provided telemetry module (`track(\"event\")`). "
            "No third-party analytics.\n",
            encoding="utf-8",
        )
    return target


def read_events(repo_dir: str | Path, days: int = 7) -> Counter:
    path = Path(repo_dir) / "data" / "events.jsonl"
    counts: Counter = Counter()
    if not path.exists():
        return counts
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if float(event.get("ts", 0)) >= cutoff:
            counts[str(event.get("event", "?"))] += 1
    return counts


def reconcile_hypotheses(repo_dir: str | Path, counts: Counter) -> list[dict]:
    """Hypothesis-ledger reconciliation: observed events become evidence
    notes; verification stays a human call, but now an informed one."""
    path = Path(repo_dir) / ".mas" / "hypotheses.yaml"
    if not path.exists():
        return []
    ledger = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    stamp = datetime.date.today().isoformat()
    for entry in ledger:
        tokens = {t.lower() for t in str(entry.get("statement", "")).split()}
        related = {e: n for e, n in counts.items() if set(e.lower().split("_")) & tokens}
        if related:
            entry["evidence_note"] = f"{stamp}: observed {dict(related)}"
    path.write_text(yaml.safe_dump(ledger, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return ledger


def generate_digest(
    repo_dir: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    days: int = 7,
) -> Path:
    root = Path(repo_dir).resolve()
    counts = read_events(root, days=days)
    ledger = reconcile_hypotheses(root, counts)
    metrics = []
    brief_path = root / "product" / "brief.yaml"
    if brief_path.exists():
        metrics = (yaml.safe_load(brief_path.read_text(encoding="utf-8")) or {}).get(
            "success_metrics", []
        )
    fallback = (
        f"# 本周产品数据 / This week\n\n事件 / events ({days}d):\n"
        + ("\n".join(f"- {e}: {n}" for e, n in counts.most_common()) or "- (no events yet)")
        + "\n\n成功指标 / success metrics:\n"
        + "\n".join(f"- {m}" for m in metrics)
    )
    text = fallback
    if counts:
        try:
            text = get_provider(provider).complete(
                model=model,
                system=f"You are the {DIGEST_MARKER}. Write a short weekly digest "
                "in the founder's language: what users did (from the event "
                "counts), how that compares to the success metrics, and ONE "
                "suggestion. Plain words. Markdown only.",
                user=yaml.safe_dump(
                    {"events": dict(counts), "success_metrics": metrics,
                     "hypotheses": ledger},
                    sort_keys=False, allow_unicode=True,
                ),
                max_tokens=1024,
            ) or fallback
        except Exception:  # noqa: BLE001
            pass
    path = root / "product" / "DIGEST.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
