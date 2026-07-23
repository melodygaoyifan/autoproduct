"""Leader synthesis (§09.4.4.7).

The Leader's most important job is filtering, not aggregating (§08.2.2.11).
This skeleton implements the deterministic half of the Leader — dedupe,
evidence filtering, blocked-voter accounting, verdict selection. The LLM
synthesis prompt (narrative summary, severity re-ranking) lands with the
full six-voter roster.
"""

from __future__ import annotations

from autoproduct.state import (
    Confidence,
    LeaderResult,
    Severity,
    Verdict,
    VoterFinding,
    VoterOutput,
    VoterStatus,
)

_ACTIONABLE_SEVERITIES = {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}


def synthesize(outputs: list[VoterOutput]) -> LeaderResult:
    blocked = [o.voter for o in outputs if o.status is not VoterStatus.OK]
    tool_failures = [
        o.voter for o in outputs if o.status is VoterStatus.BLOCKED_TOOL_FAILURE
    ]
    conflicts = [
        o.voter
        for o in outputs
        if o.status is VoterStatus.BLOCKED_REQUIREMENT_CONFLICT
    ]

    kept: list[VoterFinding] = []
    seen: set[tuple[str, int, str]] = set()
    dropped = 0
    for output in outputs:
        for finding in output.findings:
            if finding.confidence is Confidence.POSSIBLE and finding.severity not in (
                Severity.CRITICAL,
                Severity.HIGH,
            ):
                dropped += 1
                continue
            key = (finding.file_path, finding.line_start, finding.title.lower())
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
            kept.append(finding)

    kept.sort(key=lambda f: list(Severity).index(f.severity))

    # Escalation triggers, checked in priority order (§09.4.4.7 / §08.1.7 rule 10).
    if any(f.taxonomy_hint == "P6" and f.severity is Severity.CRITICAL for f in kept):
        verdict = Verdict.ESCALATE_SECURITY_RISK
    elif len(tool_failures) >= 3:
        verdict = Verdict.ESCALATE_TOOL_FAILURE
    elif conflicts:
        verdict = Verdict.ESCALATE_REQUIREMENT_CONFLICT
    elif len(blocked) >= 3:
        verdict = Verdict.ESCALATE_MISSING_CONTEXT
    elif any(f.severity in _ACTIONABLE_SEVERITIES for f in kept) or len(blocked) == 2:
        verdict = Verdict.REQUEST_CHANGES
    elif kept:
        verdict = Verdict.APPROVE_WITH_NOTES
    else:
        verdict = Verdict.APPROVE

    summary = (
        f"{len(kept)} finding(s) kept, {dropped} dropped by filter/dedupe; "
        f"{len(blocked)} of {len(outputs)} voter(s) blocked."
    )
    return LeaderResult(
        verdict=verdict,
        summary=summary,
        findings=kept,
        dropped_count=dropped,
        blocked_voters=blocked,
    )
