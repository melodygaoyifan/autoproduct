"""Gate 3 round-trip: escalation pauses at interrupt(), a decision resumes
from the SQLite checkpoint, and overrides are recorded in the audit trail.
Completion criterion #4 from §08.1.6."""

from pathlib import Path

import yaml

from autoproduct.orchestrator import is_interrupted, resume_review, run_review
from autoproduct.state import Verdict

# The mock provider marks interpolated-SELECT lines critical/P6 -> escalation.
ESCALATING_DIFF = """\
diff --git a/db.py b/db.py
--- a/db.py
+++ b/db.py
@@ -1,1 +1,2 @@
+def find(user_id):
+    return db.fetch_one(f"SELECT * FROM users WHERE id = {user_id}")
"""


def _paused_review(tmp_path, skills_dir):
    result, state = run_review(
        "fixture://hitl",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=ESCALATING_DIFF,
    )
    assert is_interrupted(state)
    assert state["leader"]["verdict"] == Verdict.ESCALATE_SECURITY_RISK.value
    # No GitHub remote in tmp repo -> issue skipped, visibly.
    assert state.get("hitl_issue_url") is None
    assert state.get("hitl_note")
    return state


def test_escalation_pauses_and_ack_resumes(tmp_path, skills_dir):
    state = _paused_review(tmp_path, skills_dir)
    result, final = resume_review(
        state["review_id"], "ack", repo_dir=str(tmp_path)
    )
    assert not is_interrupted(final)
    assert result.verdict is Verdict.ESCALATE_SECURITY_RISK
    assert final["hitl_decision"] == "ack"
    final_yaml = yaml.safe_load(
        sorted(Path(final["artifacts_dir"]).glob("*final.yaml"))[-1].read_text()
    )
    assert final_yaml["hitl"]["decision"] == "ack"


def test_override_replaces_verdict_and_leaves_trace(tmp_path, skills_dir):
    state = _paused_review(tmp_path, skills_dir)
    result, final = resume_review(
        state["review_id"], "override:REQUEST_CHANGES", repo_dir=str(tmp_path)
    )
    assert result.verdict is Verdict.REQUEST_CHANGES
    assert "human override" in result.summary
    assert "ESCALATE_SECURITY_RISK" in result.summary


def test_non_escalating_review_never_pauses(tmp_path, planted_diff_text, skills_dir):
    _, state = run_review(
        "fixture://plain",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=planted_diff_text,
    )
    assert not is_interrupted(state)
    assert (Path(state["artifacts_dir"]) / "review.md").exists()
