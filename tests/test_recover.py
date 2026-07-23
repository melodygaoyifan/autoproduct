"""Crash recovery: a review that dies mid-run continues from its SQLite
checkpoint via `autoproduct recover` — the single-instance supervision
tier below the documented Celery upgrade path."""

import shutil

import pytest

from autoproduct import testing as testing_mod
from autoproduct.orchestrator import recover_reviews, run_review

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


def test_crashed_review_recovers_from_checkpoint(
    tmp_path, planted_diff_text, skills_dir, monkeypatch
):
    calls = {"n": 0}
    original = testing_mod.run_test_gate

    def crash_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash at the test gate")
        return original(*args, **kwargs)

    monkeypatch.setattr(testing_mod, "run_test_gate", crash_once)

    with pytest.raises(RuntimeError, match="simulated crash"):
        run_review(
            "fixture://crash",
            repo_dir=str(tmp_path),
            skills_dir=skills_dir,
            provider_override="mock",
            diff_text=planted_diff_text,
        )

    results = recover_reviews(str(tmp_path))
    assert len(results) == 1
    assert results[0]["status"] == "recovered", results[0]
    assert results[0]["verdict"] == "REQUEST_CHANGES"
    # Recovery is idempotent: a finished review is not re-run.
    assert recover_reviews(str(tmp_path)) == []


ESCALATING_DIFF = """\
diff --git a/db.py b/db.py
--- a/db.py
+++ b/db.py
@@ -1,1 +1,2 @@
+def find(user_id):
+    return db.fetch_one(f"SELECT * FROM users WHERE id = {user_id}")
"""


def test_paused_hitl_review_is_not_force_recovered(tmp_path, skills_dir):
    run_review(
        "fixture://paused",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=ESCALATING_DIFF,
    )
    results = recover_reviews(str(tmp_path))
    assert results and results[0]["status"] == "awaiting_human"
