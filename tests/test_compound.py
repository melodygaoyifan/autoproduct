import datetime

import yaml

from autoproduct.compound import (
    SECTION_HEADER,
    Proposal,
    apply_to_claude_md,
    collect_signals,
    propose,
    render_proposal,
)


def _write_final(tmp_path, review_id: str, titles: list[str], verdict="REQUEST_CHANGES"):
    review_dir = tmp_path / ".mas" / "reviews" / review_id
    review_dir.mkdir(parents=True)
    (review_dir / "08-final.yaml").write_text(
        yaml.safe_dump(
            {
                "node": "final",
                "written_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "verdict": verdict,
                "findings": [
                    {"title": t, "taxonomy_hint": "P9", "severity": "high"} for t in titles
                ],
            }
        )
    )


def test_collect_signals_finds_recurrence(tmp_path):
    _write_final(tmp_path, "r1", ["Swallowed exception hides failures"])
    _write_final(tmp_path, "r2", ["Swallowed exception hides failures!"])
    _write_final(tmp_path, "r3", ["One-off finding"])
    signals = collect_signals(tmp_path, days=7)
    assert signals.review_count == 3
    assert signals.taxonomy_counts["P9"] == 3
    assert signals.recurring_titles[0][1] == 2  # normalized dedupe across runs


def test_old_reviews_fall_outside_window(tmp_path):
    review_dir = tmp_path / ".mas" / "reviews" / "old"
    review_dir.mkdir(parents=True)
    stale = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
    (review_dir / "08-final.yaml").write_text(
        yaml.safe_dump(
            {"written_at": stale.isoformat(), "verdict": "APPROVE", "findings": []}
        )
    )
    assert collect_signals(tmp_path, days=7).review_count == 0


def test_propose_via_mock_meets_evidence_bar(tmp_path):
    _write_final(tmp_path, "r1", ["Swallowed exception"])
    _write_final(tmp_path, "r2", ["Swallowed exception"])
    signals = collect_signals(tmp_path, days=7)
    proposals = propose(signals, provider="mock", model="m")
    assert proposals
    assert "Swallowed exception" in proposals[0].constraint


def test_no_signals_no_proposals(tmp_path):
    signals = collect_signals(tmp_path, days=7)
    assert propose(signals, provider="mock", model="m") == []


def test_apply_to_claude_md_is_idempotent_and_preserves_content(tmp_path):
    claude = tmp_path / "CLAUDE.md"
    claude.write_text("# Project rules\n\nAlways use uv.\n")
    p1 = [Proposal(constraint="Never swallow exceptions", rationale="seen 2x")]
    apply_to_claude_md(tmp_path, p1, date="2026-07-22")
    p2 = [Proposal(constraint="Parameterize all SQL", rationale="seen 3x")]
    apply_to_claude_md(tmp_path, p2, date="2026-07-29")
    text = claude.read_text()
    assert "Always use uv." in text                    # user content preserved
    assert text.count(SECTION_HEADER) == 1             # section replaced, not stacked
    assert "Parameterize all SQL" in text
    assert "Never swallow exceptions" not in text      # superseded window


def test_render_proposal_mentions_human_gate(tmp_path):
    _write_final(tmp_path, "r1", ["X"])
    signals = collect_signals(tmp_path, days=7)
    report = render_proposal(signals, [], date="2026-07-22")
    assert "Human-gated" in report
