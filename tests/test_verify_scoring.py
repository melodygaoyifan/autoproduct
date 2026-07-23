from autoproduct.scoring import passes_threshold, score_finding
from autoproduct.state import Confidence, Severity, VoterFinding
from autoproduct.verify import verify_finding


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


DIFF = """\
diff --git a/app/orders.py b/app/orders.py
--- a/app/orders.py
+++ b/app/orders.py
@@ -1,3 +1,6 @@
+    try:
+        db.execute("x")
+    except Exception: pass
"""


def test_mock_verifier_confirms_real_evidence():
    verdict = verify_finding(_finding(), DIFF, provider="mock", model="m")
    assert verdict == "VERIFIED"


def test_mock_verifier_refutes_fabricated_evidence():
    fabricated = _finding(evidence="os.system(user_input)  # never in diff")
    verdict = verify_finding(fabricated, DIFF, provider="mock", model="m")
    assert verdict == "NOT_REPRODUCIBLE"


def test_not_reproducible_scores_zero():
    f = _finding(verification="NOT_REPRODUCIBLE")
    assert score_finding(f, [f]) == 0


def test_verified_corroborated_scores_high():
    a = _finding(verification="VERIFIED")
    b = _finding(voter="security", line_start=6, line_end=6, verification="VERIFIED")
    a.score = score_finding(a, [a, b])
    assert a.score == 90  # 30 likely + 40 verified + 20 corroborated
    assert passes_threshold(a)


def test_solo_likely_verified_passes_only_at_high_severity():
    high = _finding(verification="VERIFIED")
    high.score = score_finding(high, [high])  # 70
    assert passes_threshold(high)  # high severity bar is 60

    medium = _finding(severity=Severity.MEDIUM, verification="VERIFIED")
    medium.score = score_finding(medium, [medium])  # 70
    assert not passes_threshold(medium)  # default bar is 80
