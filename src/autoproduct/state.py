"""Core state schemas for the review pipeline.

Implements the structured-document contract of §08.1.3 Principle 6: agents
communicate exclusively through these typed envelopes, never free-form chat.

- VoterFinding / VoterOutput: §09.4.3
- Verdict taxonomy: §09.4.4.7 (8 outcomes)
- ReviewState: the single LangGraph state dict (§09.5)
"""

from __future__ import annotations

import enum
import operator
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Confidence(str, enum.Enum):
    CERTAIN = "certain"
    LIKELY = "likely"
    POSSIBLE = "possible"


class VoterStatus(str, enum.Enum):
    """A voter that doesn't know says so — never an empty findings list
    (anti-hallucination charter rule 1, §08.1.7)."""

    OK = "OK"
    BLOCKED_MISSING_CONTEXT = "BLOCKED_MISSING_CONTEXT"
    BLOCKED_REQUIREMENT_CONFLICT = "BLOCKED_REQUIREMENT_CONFLICT"
    BLOCKED_TOOL_FAILURE = "BLOCKED_TOOL_FAILURE"


class Verdict(str, enum.Enum):
    """Leader verdict taxonomy, §09.4.4.7."""

    APPROVE = "APPROVE"
    APPROVE_WITH_NOTES = "APPROVE_WITH_NOTES"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    ESCALATE_MISSING_CONTEXT = "ESCALATE_MISSING_CONTEXT"
    ESCALATE_REQUIREMENT_CONFLICT = "ESCALATE_REQUIREMENT_CONFLICT"
    ESCALATE_SECURITY_RISK = "ESCALATE_SECURITY_RISK"
    ESCALATE_VOTER_DISAGREEMENT = "ESCALATE_VOTER_DISAGREEMENT"
    ESCALATE_TOOL_FAILURE = "ESCALATE_TOOL_FAILURE"

    @property
    def is_escalation(self) -> bool:
        return self.value.startswith("ESCALATE_")


class VoterFinding(BaseModel):
    """One candidate issue. Evidence is mandatory (charter rule 2): findings
    without a locatable quote of the actual code are filtered by the Leader."""

    voter: str
    title: str
    severity: Severity
    confidence: Confidence
    file_path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    evidence: str = Field(min_length=1, description="Verbatim quote of the offending code")
    explanation: str
    suggested_fix: str | None = None
    taxonomy_hint: str | None = Field(
        default=None, description="DAPLab pattern (P1-P9) or deterministic-tool match"
    )
    verification: str | None = Field(
        default=None, description="VERIFIED / NOT_REPRODUCIBLE / NEEDS_RUNTIME (§09.4.6)"
    )
    score: int | None = Field(
        default=None, ge=0, le=100, description="Composite confidence score (§09.4.7)"
    )


class VoterOutput(BaseModel):
    """Envelope returned by every voter invocation (§09.4.3)."""

    voter: str
    model: str
    status: VoterStatus
    substituted_from: str | None = Field(
        default=None,
        description="Set when the spec's primary provider was unavailable and "
        "the fallback ran instead — same-family substitution is visible, never silent",
    )
    findings: list[VoterFinding] = Field(default_factory=list)
    missing_sources: list[str] = Field(
        default_factory=list, description="Required when status is BLOCKED_MISSING_CONTEXT"
    )
    notes: str = ""
    duration_s: float = 0.0


class LeaderResult(BaseModel):
    verdict: Verdict
    summary: str
    findings: list[VoterFinding] = Field(default_factory=list)
    dropped_count: int = 0
    blocked_voters: list[str] = Field(default_factory=list)


class ReviewState(TypedDict, total=False):
    """Single state dict flowing through the LangGraph graph (ADR-001)."""

    review_id: str
    target: str  # PR URL or git range
    mode: str  # fast | standard | deep
    mode_override: str | None
    dor_pass: bool
    dor_reasons: list[str]
    diff: dict[str, Any]  # serialized ParsedDiff
    project_context: str  # CLAUDE.md contents, if present
    tool_reports: Annotated[list[dict[str, Any]], operator.add]
    voter_outputs: Annotated[list[dict[str, Any]], operator.add]
    verified_outputs: list[dict[str, Any]]  # voter_outputs after §09.4.6/§09.4.7
    leader: dict[str, Any]  # serialized LeaderResult
    test_report: dict[str, Any]  # Gate 2 result (§09.5.4.10)
    repo_dir: str
    hitl_issue_url: str | None
    hitl_note: str | None
    hitl_decision: str | None
    artifacts_dir: str
    error: str | None
