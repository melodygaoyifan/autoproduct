"""Markdown renderers for outward-facing artifacts.

Structured documents, not dialogue (Principle 6): the PR comment and the
HITL Issue body are template-rendered from typed state — never free-form
model output pasted onto GitHub.
"""

from __future__ import annotations

from autoproduct.state import LeaderResult, VoterOutput

_SEVERITY_ICON = {
    "critical": "🟥",
    "high": "🟧",
    "medium": "🟨",
    "low": "⬜",
    "info": "⬜",
}


def render_pr_comment(
    result: LeaderResult,
    *,
    review_id: str,
    mode: str,
    voter_outputs: list[VoterOutput],
) -> str:
    lines = [
        f"## autoproduct review — **{result.verdict.value}**",
        "",
        result.summary,
        "",
    ]
    if result.findings:
        lines += [
            "| | Location | Finding | Voter | Score |",
            "|---|---|---|---|---|",
        ]
        for f in result.findings:
            icon = _SEVERITY_ICON.get(f.severity.value, "")
            score = str(f.score) if f.score is not None else "—"
            lines.append(
                f"| {icon} {f.severity.value} | `{f.file_path}:{f.line_start}` "
                f"| {f.title} | {f.voter} | {score} |"
            )
        lines.append("")
        for f in result.findings:
            if not f.suggested_fix:
                continue
            lines += [
                "<details>",
                f"<summary>Suggested fix — {f.file_path}:{f.line_start} {f.title}</summary>",
                "",
                f.suggested_fix.strip(),
                "",
                "</details>",
                "",
            ]
    else:
        lines.append("No findings met the reporting threshold.")
        lines.append("")

    if result.blocked_voters:
        lines.append(
            f"⚠️ Blocked voters: {', '.join(result.blocked_voters)} — "
            "their perspectives are missing from this review."
        )
    substitutions = [
        f"{o.voter}: {o.substituted_from} → {o.model}"
        for o in voter_outputs
        if o.substituted_from
    ]
    if substitutions:
        lines.append("")
        lines.append(
            "<sub>Provider substitutions (bootstrap fallback): "
            + "; ".join(substitutions)
            + "</sub>"
        )
    lines += [
        "",
        f"<sub>review `{review_id}` · mode `{mode}` · {result.dropped_count} "
        "finding(s) dropped by filter/dedupe/merge · autoproduct</sub>",
    ]
    return "\n".join(lines)


def render_issue_body(
    result: LeaderResult, *, review_id: str, target: str, resume_hint: str
) -> str:
    findings = "\n".join(
        f"- **{f.severity.value}** `{f.file_path}:{f.line_start}` {f.title}"
        for f in result.findings[:10]
    ) or "- (no findings above threshold — escalation is structural)"
    return f"""## Human review required — {result.verdict.value}

**Target:** {target}
**Review:** `{review_id}`

{result.summary}

### Findings
{findings}

### Blocked voters
{", ".join(result.blocked_voters) or "none"}

### What to do
The pipeline is paused at Gate 3 (Review Gate). Resume it with:

```
{resume_hint}
```

Decisions: `ack` accepts the verdict as-is; `override:<VERDICT>` replaces it
(e.g. `override:REQUEST_CHANGES`) and is recorded in the audit trail.
"""
