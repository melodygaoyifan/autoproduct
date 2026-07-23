import pytest

from autoproduct.orchestrator import run_review
from autoproduct.replay import load_replay, summarize_step


def test_replay_reconstructs_timeline(tmp_path, planted_diff_text, skills_dir):
    _, state = run_review(
        "fixture://replay",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=planted_diff_text,
    )
    rep = load_replay(tmp_path / ".mas" / "reviews", state["review_id"])
    assert [s.node for s in rep.steps] == [
        "dor_gate", "init", "analyze", "tools", "vote", "verify", "leader", "final",
    ]
    assert rep.verdict == "REQUEST_CHANGES"
    assert rep.duration_s is not None and rep.duration_s >= 0
    assert "passed" in summarize_step(rep.steps[0])
    assert "voter(s)" in summarize_step(rep.steps[4])


def test_replay_missing_review_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_replay(tmp_path, "nope")
