from autoproduct.leader import synthesize
from autoproduct.state import (
    Confidence,
    Severity,
    Verdict,
    VoterFinding,
    VoterOutput,
    VoterStatus,
)


def _finding(**overrides) -> VoterFinding:
    base = dict(
        voter="correctness",
        title="Swallowed exception",
        severity=Severity.HIGH,
        confidence=Confidence.LIKELY,
        file_path="app/orders.py",
        line_start=5,
        line_end=5,
        evidence="except Exception: pass",
        explanation="hides failures",
    )
    base.update(overrides)
    return VoterFinding(**base)


def _output(findings=(), status=VoterStatus.OK, voter="correctness") -> VoterOutput:
    return VoterOutput(voter=voter, model="m", status=status, findings=list(findings))


def test_clean_review_approves():
    assert synthesize([_output()]).verdict is Verdict.APPROVE


def test_high_finding_requests_changes():
    result = synthesize([_output([_finding()])])
    assert result.verdict is Verdict.REQUEST_CHANGES
    assert result.findings


def test_duplicate_findings_deduped():
    result = synthesize([_output([_finding(), _finding()])])
    assert len(result.findings) == 1
    assert result.dropped_count == 1


def test_low_confidence_low_severity_dropped():
    weak = _finding(severity=Severity.LOW, confidence=Confidence.POSSIBLE)
    result = synthesize([_output([weak])])
    assert result.verdict is Verdict.APPROVE
    assert result.dropped_count == 1


def test_critical_security_escalates():
    sec = _finding(severity=Severity.CRITICAL, taxonomy_hint="P6", title="SQL injection")
    assert synthesize([_output([sec])]).verdict is Verdict.ESCALATE_SECURITY_RISK


def test_three_blocked_voters_escalates_missing_context():
    outputs = [
        _output(status=VoterStatus.BLOCKED_MISSING_CONTEXT, voter=f"v{i}")
        for i in range(3)
    ]
    assert synthesize(outputs).verdict is Verdict.ESCALATE_MISSING_CONTEXT
