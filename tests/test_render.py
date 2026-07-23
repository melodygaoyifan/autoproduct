from autoproduct.render import render_issue_body, render_pr_comment
from autoproduct.state import (
    Confidence,
    LeaderResult,
    Severity,
    Verdict,
    VoterFinding,
    VoterOutput,
    VoterStatus,
)

FINDING = VoterFinding(
    voter="security",
    title="SQL injection in get_order",
    severity=Severity.CRITICAL,
    confidence=Confidence.CERTAIN,
    file_path="orders.py",
    line_start=2,
    line_end=2,
    evidence='f"SELECT * FROM orders WHERE id = {order_id}"',
    explanation="interpolated SQL",
    suggested_fix="use a parameterized query",
    score=90,
)

RESULT = LeaderResult(
    verdict=Verdict.ESCALATE_SECURITY_RISK,
    summary="One critical injection.",
    findings=[FINDING],
    dropped_count=3,
    blocked_voters=["performance"],
)


def test_pr_comment_contains_all_load_bearing_parts():
    substituted = VoterOutput(
        voter="security",
        model="claude-sonnet-5",
        status=VoterStatus.OK,
        substituted_from="openai/gpt-5.4 (no key)",
    )
    comment = render_pr_comment(
        RESULT, review_id="abc123", mode="standard", voter_outputs=[substituted]
    )
    assert "ESCALATE_SECURITY_RISK" in comment
    assert "`orders.py:2`" in comment
    assert "Suggested fix" in comment
    assert "Blocked voters: performance" in comment
    assert "openai/gpt-5.4" in comment  # substitution is visible on the PR
    assert "review `abc123`" in comment


def test_issue_body_has_resume_instructions():
    body = render_issue_body(
        RESULT,
        review_id="abc123",
        target="https://github.com/x/y/pull/1",
        resume_hint="autoproduct resume abc123 --decision ack",
    )
    assert "Gate 3" in body
    assert "autoproduct resume abc123" in body
    assert "override:<VERDICT>" in body
