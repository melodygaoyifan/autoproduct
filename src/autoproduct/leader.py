"""Leader synthesis (§09.4.4.7).

The Leader's most important job is filtering, not aggregating (§08.2.2.11).
Two halves:

- Deterministic (synthesize): score/evidence filtering, exact-key dedupe,
  blocked-voter accounting, verdict selection. Always runs; the system
  never depends on an LLM for control flow (Principle 1).
- LLM (semantic_merge): clusters paraphrased duplicates that exact-key
  dedupe cannot see (six voters describing one missing-WHERE bug five
  ways) and writes the narrative summary. A different model invocation
  from any voter (charter rule 5). On any failure it degrades to the
  deterministic result — the LLM can only improve the report, never
  gate it.
"""

from __future__ import annotations

import yaml

from autoproduct import scoring
from autoproduct.providers import get_provider
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


def _keep(finding: VoterFinding, all_findings: list[VoterFinding]) -> bool:
    if finding.verification is None:
        # Verification pass didn't run (fast mode): fall back to the
        # coarse self-confidence filter.
        return not (
            finding.confidence is Confidence.POSSIBLE
            and finding.severity not in (Severity.CRITICAL, Severity.HIGH)
        )
    if finding.score is None:
        finding.score = scoring.score_finding(finding, all_findings)
    return scoring.passes_threshold(finding)


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

    all_findings = [f for o in outputs for f in o.findings]
    kept: list[VoterFinding] = []
    seen: set[tuple[str, int, str]] = set()
    dropped = 0
    for output in outputs:
        for finding in output.findings:
            if not _keep(finding, all_findings):
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


LEADER_MARKER = "review leader synthesizing"

_LEADER_SYSTEM = f"""You are the {LEADER_MARKER} findings from independent
reviewers. Several findings may describe the SAME underlying defect in
different words (same root cause, overlapping location). Your only tasks:

1. Group the numbered findings into clusters of same-defect duplicates.
   Every finding index appears in exactly one cluster; a finding with no
   duplicate is its own cluster. Never cluster findings that describe
   different defects, even in the same lines.
2. Write a 2-3 sentence reviewer-facing summary of the real issues.

Respond with ONLY a YAML document:
clusters: [[1, 4], [2], [3, 5]]
summary: ...
"""


def semantic_merge(
    result: LeaderResult, *, provider: str, model: str
) -> LeaderResult:
    if len(result.findings) < 2:
        return result
    listing = "\n".join(
        f"{i + 1}. [{f.severity.value}] {f.file_path}:{f.line_start}-{f.line_end}"
        f" ({f.voter}) {f.title} — {f.explanation.strip()}"
        for i, f in enumerate(result.findings)
    )
    try:
        raw = get_provider(provider).complete(
            model=model,
            system=_LEADER_SYSTEM,
            user=f"<findings>\n{listing}\n</findings>",
            max_tokens=2048,
        )
        data = yaml.safe_load(raw.strip().strip("`"))
        clusters = data["clusters"]
        indices = sorted(i for cluster in clusters for i in cluster)
        if indices != list(range(1, len(result.findings) + 1)):
            raise ValueError(f"clusters are not a partition: {clusters}")
    except Exception:  # noqa: BLE001 — LLM half never gates the pipeline
        return result

    merged: list[VoterFinding] = []
    for cluster in clusters:
        members = [result.findings[i - 1] for i in cluster]
        members.sort(
            key=lambda f: (list(Severity).index(f.severity), -(f.score or 0))
        )
        representative = members[0]
        others = sorted({m.voter for m in members[1:]} - {representative.voter})
        if others:
            representative.explanation = (
                f"{representative.explanation.rstrip()}\n"
                f"(Independently flagged by: {', '.join(others)}.)"
            )
        merged.append(representative)

    merged.sort(key=lambda f: list(Severity).index(f.severity))
    dropped_as_duplicates = len(result.findings) - len(merged)
    return result.model_copy(
        update={
            "findings": merged,
            "dropped_count": result.dropped_count + dropped_as_duplicates,
            "summary": str(data.get("summary", "")).strip() or result.summary,
        }
    )
