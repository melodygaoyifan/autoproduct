"""Deployment Review MAS (§09.11) — insight/assistive tiers.

Reuses the review-stage machinery wholesale: the same Voter class over a
deploy-specific skills directory, the same fresh-agent verification, the
same scoring. What differs is the verdict taxonomy and the policy input:

- Policy-as-Prompt: `.mas/deploy-policy.yaml` is compiled into voter
  context, and its `forbidden` entries are enforced deterministically too.
- Trust tier ceiling: this stage RECOMMENDS. PROMOTE means "nothing blocks
  promotion", never "promoted" — production deploys stay human-executed
  forever (§08.1.8, hard architectural ceiling).
"""

from __future__ import annotations

import enum
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autoproduct import scoring, verify
from autoproduct.deploy.probes import migration_scan, workflow_scan
from autoproduct.diff import ParsedDiff, fetch_diff, parse_unified_diff
from autoproduct.mirror import YamlMirror
from autoproduct.state import Severity, VoterFinding, VoterOutput, VoterStatus
from autoproduct.voters import load_voters

DEFAULT_POLICY = {
    "tier": "insight",  # insight | assistive (autonomous requires track record, v0.8+)
    "forbidden": [
        "permissions: write-all",
        "pull_request_target",
        "--privileged",
    ],
    "require_rollback_note": True,
}


class DeployVerdict(str, enum.Enum):
    PROMOTE = "PROMOTE"  # recommendation only — human executes
    HOLD_FOR_HUMAN = "HOLD_FOR_HUMAN"
    ESCALATE_DEPLOY_RISK = "ESCALATE_DEPLOY_RISK"
    ESCALATE_MIGRATION_DESTRUCTIVE = "ESCALATE_MIGRATION_DESTRUCTIVE"
    ESCALATE_POLICY_VIOLATION = "ESCALATE_POLICY_VIOLATION"


class DeployResult(BaseModel):
    verdict: DeployVerdict
    tier: str
    summary: str
    findings: list[VoterFinding] = Field(default_factory=list)
    blocked_voters: list[str] = Field(default_factory=list)
    deploy_files: list[str] = Field(default_factory=list)
    artifacts_dir: str = ""


def load_policy(repo_dir: str | Path) -> dict:
    path = Path(repo_dir) / ".mas" / "deploy-policy.yaml"
    if not path.exists():
        return dict(DEFAULT_POLICY)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {**DEFAULT_POLICY, **loaded}


def _policy_prompt(policy: dict) -> str:
    forbidden = "\n".join(f"- {f}" for f in policy["forbidden"])
    return (
        "Deploy policy for this project (violations are findings of severity "
        f"critical, taxonomy_hint 'deploy:policy'):\nForbidden patterns:\n{forbidden}\n"
        + (
            "Every migration must state its rollback path in the PR.\n"
            if policy.get("require_rollback_note")
            else ""
        )
    )


def _policy_violations(diff: ParsedDiff, policy: dict) -> list[VoterFinding]:
    """The deterministic half of Policy-as-Prompt: `forbidden` strings are
    enforced by code even if every voter misses them."""
    findings = []
    for file in diff.files:
        for lineno, text in file.added:
            for forbidden in policy["forbidden"]:
                if forbidden in text:
                    findings.append(
                        VoterFinding(
                            voter="tool:deploy_policy",
                            title=f"Forbidden by deploy policy: {forbidden}",
                            severity=Severity.CRITICAL,
                            confidence="certain",
                            file_path=file.path,
                            line_start=lineno,
                            line_end=lineno,
                            evidence=text.strip()[:200],
                            explanation="This exact pattern is on the project's "
                            "deploy-policy forbidden list (.mas/deploy-policy.yaml).",
                            taxonomy_hint="deploy:policy",
                            verification="VERIFIED",
                            score=100,
                        )
                    )
    return findings


def decide(findings: list[VoterFinding], blocked: list[str]) -> DeployVerdict:
    """Deterministic verdict selection — priority order mirrors §09.11.6."""
    hints = {f.taxonomy_hint for f in findings}
    if "deploy:policy" in hints:
        return DeployVerdict.ESCALATE_POLICY_VIOLATION
    if any(
        f.taxonomy_hint == "deploy:migration" and f.severity is Severity.CRITICAL
        for f in findings
    ):
        return DeployVerdict.ESCALATE_MIGRATION_DESTRUCTIVE
    if any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings):
        return DeployVerdict.ESCALATE_DEPLOY_RISK
    if findings or len(blocked) >= 2:
        return DeployVerdict.HOLD_FOR_HUMAN
    return DeployVerdict.PROMOTE


def run_deploy_review(
    target: str,
    *,
    repo_dir: str = ".",
    skills_dir: str = "skills/deploy",
    provider_override: str | None = None,
    diff_text: str | None = None,
) -> DeployResult:
    started = time.monotonic()
    diff = (
        parse_unified_diff(diff_text)
        if diff_text is not None
        else fetch_diff(target, repo_dir=repo_dir)
    )
    policy = load_policy(repo_dir)
    review_id = uuid.uuid4().hex[:12]
    mirror = YamlMirror(Path(repo_dir) / ".mas" / "deploy-reviews", review_id)

    from autoproduct.deploy.probes import detect_deploy_files

    deploy_files = detect_deploy_files(diff.changed_files)

    reports = [migration_scan(diff, repo_dir), workflow_scan(diff, repo_dir)]
    findings: list[VoterFinding] = [f for r in reports for f in r.findings]
    findings += _policy_violations(diff, policy)
    mirror.write(
        "probes",
        {"reports": [r.model_dump(mode="json") for r in reports],
         "policy_violations": sum(1 for f in findings if f.taxonomy_hint == "deploy:policy")},
    )

    voters = load_voters(skills_dir, provider_override=provider_override)
    context = _policy_prompt(policy)
    with ThreadPoolExecutor(max_workers=len(voters)) as pool:
        outputs = list(
            pool.map(
                lambda v: v.run(diff.raw, context=context, repo_dir=repo_dir), voters
            )
        )
    mirror.write("vote", {"voter_outputs": [o.model_dump(mode="json") for o in outputs]})

    voter_findings = [f for o in outputs for f in o.findings]
    skills = {v.spec.name: v.skill for v in voters}
    todo = [f for f in voter_findings if f.verification is None]

    def check(finding: VoterFinding) -> None:
        provider, model, fallback = verify.verifier_config_for(skills[finding.voter])
        if provider_override:
            provider, fallback = provider_override, None
        finding.verification = verify.verify_finding(
            finding, diff.raw, provider=provider, model=model, fallback=fallback
        )

    if todo:
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
            list(pool.map(check, todo))
        everything = findings + voter_findings
        for finding in todo:
            finding.score = scoring.score_finding(finding, everything)

    kept = findings + [
        f
        for f in voter_findings
        if f.verification != "NOT_REPRODUCIBLE" and scoring.passes_threshold(f)
    ]
    kept.sort(key=lambda f: list(Severity).index(f.severity))
    blocked = [o.voter for o in outputs if o.status is not VoterStatus.OK]

    verdict = decide(kept, blocked)

    from autoproduct.deploy import track_record

    track_record.record_review(repo_dir, review_id, verdict.value)
    ready = track_record.readiness(
        repo_dir, needed=int(policy.get("promotion_track_record", 10))
    )
    tier_note = ""
    if policy["tier"] == "insight" and ready.eligible:
        tier_note = (
            f"; track record {ready.streak}/{ready.needed} correct PROMOTEs — "
            "eligible for assistive tier (human edits .mas/deploy-policy.yaml)"
        )

    result = DeployResult(
        verdict=verdict,
        tier=policy["tier"],
        summary=(
            f"{verdict.value} (tier: {policy['tier']}; recommendation only) — "
            f"{len(kept)} finding(s), {len(blocked)} blocked voter(s), "
            f"{len(deploy_files)} deploy-relevant file(s)"
            f"{tier_note}, {time.monotonic() - started:.0f}s"
        ),
        findings=kept,
        blocked_voters=blocked,
        deploy_files=deploy_files,
        artifacts_dir=str(mirror.dir),
    )
    mirror.write("final", result.model_dump(mode="json"))
    return result
