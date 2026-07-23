from autoproduct.leader import semantic_merge, synthesize
from autoproduct.state import Confidence, Severity, VoterFinding, VoterOutput, VoterStatus


def _finding(voter, title, line, severity=Severity.CRITICAL, **overrides) -> VoterFinding:
    base = dict(
        voter=voter,
        title=title,
        severity=severity,
        confidence=Confidence.CERTAIN,
        file_path="app/orders.py",
        line_start=line,
        line_end=line,
        evidence="db.execute(\"UPDATE orders SET status = 'cancelled'\")",
        explanation="update misses WHERE clause",
        verification="VERIFIED",
        score=80,
    )
    base.update(overrides)
    return VoterFinding(**base)


def test_paraphrased_duplicates_merge():
    outputs = [
        VoterOutput(
            voter="correctness",
            model="m",
            status=VoterStatus.OK,
            findings=[_finding("correctness", "cancel_order updates ALL orders", 6)],
        ),
        VoterOutput(
            voter="security",
            model="m",
            status=VoterStatus.OK,
            findings=[_finding("security", "Missing WHERE clause causes mass update", 5)],
        ),
        VoterOutput(
            voter="performance",
            model="m",
            status=VoterStatus.OK,
            findings=[
                _finding(
                    "performance",
                    "Unbounded full-table write",
                    4,
                    severity=Severity.HIGH,
                )
            ],
        ),
    ]
    deterministic = synthesize(outputs)
    assert len(deterministic.findings) == 3  # exact-key dedupe can't see these

    merged = semantic_merge(deterministic, provider="mock", model="leader")
    assert len(merged.findings) == 1
    representative = merged.findings[0]
    assert representative.severity is Severity.CRITICAL
    assert "Independently flagged by" in representative.explanation
    assert merged.dropped_count == deterministic.dropped_count + 2
    assert merged.summary == "mock leader summary"


def test_distinct_defects_not_merged():
    outputs = [
        VoterOutput(
            voter="correctness",
            model="m",
            status=VoterStatus.OK,
            findings=[
                _finding("correctness", "Missing WHERE clause", 5),
                _finding(
                    "correctness",
                    "eval of user input",
                    50,
                    evidence="eval(request.args.get('formula'))",
                    explanation="arbitrary code execution",
                ),
            ],
        ),
    ]
    merged = semantic_merge(synthesize(outputs), provider="mock", model="leader")
    assert len(merged.findings) == 2


def test_llm_failure_degrades_to_deterministic():
    outputs = [
        VoterOutput(
            voter="correctness",
            model="m",
            status=VoterStatus.OK,
            findings=[
                _finding("correctness", "a", 5),
                _finding("security", "b", 6),
            ],
        ),
    ]
    deterministic = synthesize(outputs)
    # Unreachable provider: anthropic without a key raises inside
    # semantic_merge, which must swallow it and return the input unchanged.
    merged = semantic_merge(deterministic, provider="anthropic", model="x")
    assert merged == deterministic
