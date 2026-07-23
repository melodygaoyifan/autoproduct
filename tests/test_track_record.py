from autoproduct.deploy.track_record import (
    mark_outcome,
    readiness,
    record_review,
)


def _promote(tmp_path, review_id, outcome=None):
    record_review(tmp_path, review_id, "PROMOTE")
    if outcome:
        mark_outcome(tmp_path, review_id, outcome)


def test_streak_counts_consecutive_correct_promotes(tmp_path):
    for i in range(3):
        _promote(tmp_path, f"r{i}", "correct")
    ready = readiness(tmp_path, needed=3)
    assert ready.streak == 3 and ready.eligible


def test_incorrect_resets_streak(tmp_path):
    _promote(tmp_path, "r0", "correct")
    _promote(tmp_path, "r1", "incorrect")
    _promote(tmp_path, "r2", "correct")
    ready = readiness(tmp_path, needed=3)
    assert ready.streak == 1 and not ready.eligible


def test_unmarked_reviews_do_not_count(tmp_path):
    _promote(tmp_path, "r0", "correct")
    _promote(tmp_path, "r1")  # human never marked it
    ready = readiness(tmp_path, needed=1)
    assert ready.streak == 1 and ready.marked_total == 1


def test_mark_unknown_review_returns_false(tmp_path):
    assert mark_outcome(tmp_path, "ghost", "correct") is False


def test_duplicate_record_ignored(tmp_path):
    record_review(tmp_path, "r0", "PROMOTE")
    record_review(tmp_path, "r0", "PROMOTE")
    mark_outcome(tmp_path, "r0", "correct")
    assert readiness(tmp_path, needed=1).marked_total == 1
