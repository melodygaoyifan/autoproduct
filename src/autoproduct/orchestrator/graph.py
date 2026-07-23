"""The review state machine (§09.5).

Deterministic control flow, probabilistic analysis (Principle 1): this
module decides what runs next; LLMs only ever run inside voter nodes.

Current graph:

    dor_gate -> init -> analyze -> tools -> vote -> verify -> leader -> post
        \\-(not ready)-> post          (escalation) -> escalate -> hitl -> post

Gate 3 (Review Gate): ESCALATE_* verdicts open a GitHub Issue and pause at
`hitl` via interrupt(); `autoproduct resume <review-id> --decision ...`
continues from the SQLite checkpoint. peer / adversarial_test nodes land in
later milestones.
"""

from __future__ import annotations

import functools
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml as yaml_lib
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from autoproduct import github, render, testing

from autoproduct import leader as leader_mod
from autoproduct import scoring, verify
from autoproduct.diff import ParsedDiff, fetch_diff, parse_unified_diff
from autoproduct.mirror import YamlMirror
from autoproduct.orchestrator.mode_router import select_mode
from autoproduct.state import (
    LeaderResult,
    ReviewState,
    Verdict,
    VoterFinding,
    VoterOutput,
    VoterStatus,
)
from autoproduct.tools import run_all
from autoproduct.tools.base import render_for_context
from autoproduct.voters import load_voters

MAX_REVIEWABLE_LINES = 2000

# fast mode = the single cheap reviewer from §08.3.5 (style runs on Haiku).
FAST_MODE_ROSTER = {"style"}

# The Leader is its own model invocation, never one of the voters (charter
# rule 5). Engineering default per the roster in §09.4.
LEADER_PROVIDER = ("anthropic", "claude-opus-4-8")


def dor_gate_node(state: ReviewState, *, repo_dir: str) -> dict[str, Any]:
    """Gate 1 — Definition of Ready. Cheap deterministic checks before any
    LLM spend."""
    target = state["target"]
    if state.get("diff"):
        diff = parse_unified_diff(state["diff"]["raw"])
    else:
        diff = fetch_diff(target, repo_dir=repo_dir)

    reasons = []
    if not diff.files:
        reasons.append("empty diff — nothing to review")
    if diff.changed_lines > MAX_REVIEWABLE_LINES:
        reasons.append(
            f"diff too large ({diff.changed_lines} lines > {MAX_REVIEWABLE_LINES}); split the PR"
        )
    return {
        "dor_pass": not reasons,
        "dor_reasons": reasons,
        "diff": diff.to_dict(),
    }


def init_node(state: ReviewState, *, repo_dir: str) -> dict[str, Any]:
    claude_md = Path(repo_dir) / "CLAUDE.md"
    context = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    return {"project_context": context}


def analyze_node(state: ReviewState) -> dict[str, Any]:
    diff = parse_unified_diff(state["diff"]["raw"])
    return {"mode": select_mode(diff, state.get("mode_override"))}


def tools_node(state: ReviewState, *, repo_dir: str) -> dict[str, Any]:
    """§09.7.3: deterministic analyzers run before any voter. Their findings
    enter the pipeline pre-verified and their summary feeds voter context.
    Skipped in fast mode — the cheap path stays cheap."""
    if state.get("mode") == "fast":
        return {"tool_reports": []}
    diff = parse_unified_diff(state["diff"]["raw"])
    reports = run_all(diff, repo_dir)
    envelopes = [
        VoterOutput(
            voter=f"tool:{r.tool}",
            model="deterministic",
            status=VoterStatus.OK,
            findings=r.findings,
        )
        for r in reports
        if r.findings
    ]
    return {
        "tool_reports": [r.model_dump(mode="json") for r in reports],
        "voter_outputs": [e.model_dump(mode="json") for e in envelopes],
    }


def vote_node(
    state: ReviewState, *, skills_dir: str, provider_override: str | None, repo_dir: str
) -> dict[str, Any]:
    voters = load_voters(skills_dir, provider_override=provider_override)
    if state.get("mode") == "fast":
        fast = [v for v in voters if v.spec.name in FAST_MODE_ROSTER]
        voters = fast or voters[:1]
    diff_raw = state["diff"]["raw"]
    context = state.get("project_context", "")
    from autoproduct.tools import ToolReport

    tool_summary = render_for_context(
        [ToolReport.model_validate(r) for r in state.get("tool_reports", [])]
    )
    if tool_summary:
        context = f"{context}\n\n{tool_summary}" if context else tool_summary
    with ThreadPoolExecutor(max_workers=len(voters)) as pool:
        outputs = list(
            pool.map(
                lambda v: v.run(diff_raw, context=context, repo_dir=repo_dir), voters
            )
        )
    return {"voter_outputs": [o.model_dump(mode="json") for o in outputs]}


def verify_node(
    state: ReviewState, *, skills_dir: str, provider_override: str | None
) -> dict[str, Any]:
    """§09.4.6: every finding re-examined by a fresh agent, then scored
    (§09.4.7). Skipped in fast mode — the cheap path stays cheap."""
    outputs = [VoterOutput.model_validate(o) for o in state["voter_outputs"]]
    if state.get("mode") == "fast":
        return {"verified_outputs": [o.model_dump(mode="json") for o in outputs]}

    skills = {v.spec.name: v.skill for v in load_voters(skills_dir)}
    diff_raw = state["diff"]["raw"]

    def check(finding: VoterFinding) -> None:
        provider, model, fallback = verify.verifier_config_for(skills[finding.voter])
        if provider_override:
            provider, fallback = provider_override, None
        finding.verification = verify.verify_finding(
            finding, diff_raw, provider=provider, model=model, fallback=fallback
        )

    all_findings = [f for o in outputs for f in o.findings]
    # Tool findings arrive pre-verified and pre-scored; only voter findings
    # get the fresh-agent pass (and tool findings still count as corroboration).
    todo = [f for f in all_findings if f.verification is None and f.voter in skills]
    if todo:
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
            list(pool.map(check, todo))
        for finding in todo:
            finding.score = scoring.score_finding(finding, all_findings)
    return {"verified_outputs": [o.model_dump(mode="json") for o in outputs]}


def leader_node(state: ReviewState, *, provider_override: str | None) -> dict[str, Any]:
    raw = state.get("verified_outputs") or state["voter_outputs"]
    outputs = [VoterOutput.model_validate(o) for o in raw]
    result = leader_mod.synthesize(outputs)
    if state.get("mode") != "fast":
        provider, model = (
            (provider_override, "leader") if provider_override else LEADER_PROVIDER
        )
        result = leader_mod.semantic_merge(result, provider=provider, model=model)
    return {"leader": result.model_dump(mode="json")}


def test_gate_node(state: ReviewState, *, repo_dir: str) -> dict[str, Any]:
    """Gate 2 — Test Gate. An APPROVE-class verdict cannot survive a failing
    suite; the downgrade is deterministic code, not model judgment."""
    if state.get("mode") == "fast":
        return {
            "test_report": testing.TestReport(
                status="skipped", summary="fast mode skips the test gate"
            ).model_dump(mode="json")
        }
    report = testing.run_test_gate(
        repo_dir,
        state["diff"]["raw"],
        mode=state.get("mode", "standard"),
        changed_files=state["diff"].get("changed_files", []),
    )
    update: dict[str, Any] = {"test_report": report.model_dump(mode="json")}
    verdict = Verdict(state["leader"]["verdict"])
    if report.gate_blocks and verdict in (
        Verdict.APPROVE,
        Verdict.APPROVE_WITH_NOTES,
    ):
        leader = dict(state["leader"])
        leader["verdict"] = Verdict.REQUEST_CHANGES.value
        if report.mutation and report.mutation.status == "failed":
            reason = report.mutation.summary
        else:
            reason = f"{report.status}: {report.summary}"
        leader["summary"] = f"[Gate 2 blocked — {reason}] " + leader["summary"]
        update["leader"] = leader
    return update


def escalate_node(state: ReviewState) -> dict[str, Any]:
    """Open the HITL issue. Separate from hitl_node so the side effect runs
    exactly once — interrupt() re-executes its own node body on resume."""
    result = LeaderResult.model_validate(state["leader"])
    resume_hint = f"autoproduct resume {state['review_id']} --decision ack"
    body = render.render_issue_body(
        result,
        review_id=state["review_id"],
        target=state["target"],
        resume_hint=resume_hint,
    )
    issue_url, note = github.create_issue(
        state.get("repo_dir", "."),
        f"[autoproduct] {result.verdict.value}: review {state['review_id']}",
        body,
    )
    return {"hitl_issue_url": issue_url, "hitl_note": note}


def hitl_node(state: ReviewState) -> dict[str, Any]:
    """Gate 3 — pause for the human. Decision: 'ack' keeps the verdict,
    'override:<VERDICT>' replaces it, recorded in the audit trail."""
    decision = interrupt(
        {
            "review_id": state["review_id"],
            "verdict": state["leader"]["verdict"],
            "issue_url": state.get("hitl_issue_url"),
        }
    )
    decision = str(decision).strip()
    update: dict[str, Any] = {"hitl_decision": decision}
    if decision.startswith("override:"):
        new_verdict = Verdict(decision.split(":", 1)[1].strip())
        leader = dict(state["leader"])
        leader["summary"] = (
            f"[human override: {leader['verdict']} → {new_verdict.value}] "
            + leader["summary"]
        )
        leader["verdict"] = new_verdict.value
        update["leader"] = leader
    return update


def _deploy_files(state: ReviewState) -> list[str]:
    """Gate 5 trigger (§08.3.3): deploy-relevant files in the diff mean the
    PR should also pass Deployment Review before promotion."""
    from autoproduct.deploy import detect_deploy_files

    return detect_deploy_files(state.get("diff", {}).get("changed_files", []))


def _append_voter_logs(state: ReviewState, outputs: list[VoterOutput]) -> None:
    """Per-voter log (§09.8.5): one appended entry per invocation, the raw
    material the weekly compounding loop aggregates."""
    base = Path(state.get("repo_dir", ".")) / ".mas" / "voters"
    for output in outputs:
        if output.voter.startswith("tool:"):
            continue
        log_path = base / output.voter / "log.yaml"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "review_id": state["review_id"],
            "model": output.model,
            "status": output.status.value,
            "substituted_from": output.substituted_from,
            "findings": len(output.findings),
            "duration_s": round(output.duration_s, 2),
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(yaml_lib.safe_dump([entry], sort_keys=False))


def post_node(state: ReviewState, *, mirror: YamlMirror) -> dict[str, Any]:
    if not state.get("dor_pass"):
        mirror.write("dor_fail", {"reasons": state.get("dor_reasons", [])})
        return {"artifacts_dir": str(mirror.dir)}
    result = LeaderResult.model_validate(state["leader"])
    outputs = [
        VoterOutput.model_validate(o)
        for o in (state.get("verified_outputs") or state.get("voter_outputs") or [])
    ]
    _append_voter_logs(state, outputs)
    comment = render.render_pr_comment(
        result,
        review_id=state["review_id"],
        mode=state.get("mode", "standard"),
        voter_outputs=outputs,
        test_report=state.get("test_report"),
    )
    (mirror.dir / "review.md").write_text(comment, encoding="utf-8")
    comment_note = github.post_pr_comment(state["target"], comment)
    mirror.write(
        "final",
        {
            "review_id": state["review_id"],
            "target": state["target"],
            "mode": state.get("mode"),
            "verdict": state["leader"]["verdict"],
            "summary": state["leader"]["summary"],
            "findings": state["leader"]["findings"],
            "blocked_voters": state["leader"]["blocked_voters"],
            "test_report": state.get("test_report"),
            "deploy_review_recommended": _deploy_files(state),
            "hitl": {
                "issue_url": state.get("hitl_issue_url"),
                "note": state.get("hitl_note"),
                "decision": state.get("hitl_decision"),
            },
            "pr_comment": {"posted": comment_note is None, "note": comment_note},
        },
    )
    return {"artifacts_dir": str(mirror.dir)}


def build_graph(
    *,
    repo_dir: str = ".",
    skills_dir: str = "skills",
    artifacts_dir: str = ".mas/reviews",
    provider_override: str | None = None,
    review_id: str | None = None,
):
    review_id = review_id or uuid.uuid4().hex[:12]
    mirror = YamlMirror(Path(repo_dir) / artifacts_dir, review_id)

    def mirrored(name: str, fn):
        @functools.wraps(fn)
        def wrapper(state: ReviewState) -> dict[str, Any]:
            update = fn(state)
            if name != "post":
                mirror.write(name, _mirror_view(name, update))
            return update

        return wrapper

    graph = StateGraph(ReviewState)
    graph.add_node(
        "dor_gate", mirrored("dor_gate", functools.partial(dor_gate_node, repo_dir=repo_dir))
    )
    graph.add_node("init", mirrored("init", functools.partial(init_node, repo_dir=repo_dir)))
    graph.add_node("analyze", mirrored("analyze", analyze_node))
    graph.add_node(
        "tools", mirrored("tools", functools.partial(tools_node, repo_dir=repo_dir))
    )
    graph.add_node(
        "vote",
        mirrored(
            "vote",
            functools.partial(
                vote_node,
                skills_dir=skills_dir,
                provider_override=provider_override,
                repo_dir=repo_dir,
            ),
        ),
    )
    graph.add_node(
        "verify",
        mirrored(
            "verify",
            functools.partial(
                verify_node, skills_dir=skills_dir, provider_override=provider_override
            ),
        ),
    )
    graph.add_node(
        "leader",
        mirrored(
            "leader", functools.partial(leader_node, provider_override=provider_override)
        ),
    )
    graph.add_node(
        "test_gate",
        mirrored("test_gate", functools.partial(test_gate_node, repo_dir=repo_dir)),
    )
    graph.add_node("escalate", mirrored("escalate", escalate_node))
    graph.add_node("hitl", hitl_node)
    graph.add_node("post", functools.partial(post_node, mirror=mirror))

    graph.set_entry_point("dor_gate")
    graph.add_conditional_edges(
        "dor_gate", lambda s: "init" if s["dor_pass"] else "post"
    )
    graph.add_edge("init", "analyze")
    graph.add_edge("analyze", "tools")
    graph.add_edge("tools", "vote")
    graph.add_edge("vote", "verify")
    graph.add_conditional_edges(
        "leader",
        lambda s: "escalate"
        if Verdict(s["leader"]["verdict"]).is_escalation
        else "test_gate",
    )
    graph.add_edge("verify", "leader")
    graph.add_edge("test_gate", "post")
    graph.add_edge("escalate", "hitl")
    graph.add_edge("hitl", "post")
    graph.add_edge("post", END)

    checkpoint_db = Path(repo_dir) / ".mas" / "checkpoints.db"
    checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(checkpoint_db, check_same_thread=False))
    return graph.compile(checkpointer=saver), review_id


def _mirror_view(name: str, update: dict[str, Any]) -> dict[str, Any]:
    # Keep mirror files readable: elide the raw diff blob.
    view = dict(update)
    if "diff" in view:
        view["diff"] = {k: v for k, v in view["diff"].items() if k != "raw"}
    if "project_context" in view:
        view["project_context"] = f"{len(view['project_context'])} chars"
    return view


def run_review(
    target: str,
    *,
    repo_dir: str = ".",
    skills_dir: str = "skills",
    provider_override: str | None = None,
    mode_override: str | None = None,
    diff_text: str | None = None,
) -> tuple[LeaderResult | None, ReviewState]:
    app, review_id = build_graph(
        repo_dir=repo_dir,
        skills_dir=skills_dir,
        provider_override=provider_override,
    )
    meta = {
        "target": target,
        "repo_dir": repo_dir,
        "skills_dir": skills_dir,
        "provider_override": provider_override,
    }
    meta_path = Path(repo_dir) / ".mas" / "reviews" / review_id / "meta.yaml"
    meta_path.write_text(yaml_lib.safe_dump(meta), encoding="utf-8")

    initial: ReviewState = {
        "review_id": review_id,
        "target": target,
        "mode_override": mode_override,
        "repo_dir": repo_dir,
    }
    if diff_text is not None:
        initial["diff"] = {"raw": diff_text}
    final = app.invoke(initial, config={"configurable": {"thread_id": review_id}})
    result = (
        LeaderResult.model_validate(final["leader"]) if final.get("leader") else None
    )
    return result, final


def is_interrupted(state: ReviewState) -> bool:
    return "__interrupt__" in state


def recover_reviews(repo_dir: str = ".") -> list[dict]:
    """Crash recovery (single-instance supervision): reviews with a
    meta.yaml but no final.yaml continue from their SQLite checkpoint —
    LangGraph re-invokes from the last completed super-step. Reviews that
    never checkpointed are reported, not guessed at. The Celery supervisor
    remains the multi-instance upgrade path."""
    reviews_dir = Path(repo_dir) / ".mas" / "reviews"
    results = []
    if not reviews_dir.is_dir():
        return results
    for review_dir in sorted(reviews_dir.iterdir()):
        meta_path = review_dir / "meta.yaml"
        if not meta_path.exists() or list(review_dir.glob("[0-9]*-final.yaml")):
            continue
        review_id = review_dir.name
        meta = yaml_lib.safe_load(meta_path.read_text(encoding="utf-8"))
        try:
            app, _ = build_graph(
                repo_dir=meta["repo_dir"],
                skills_dir=meta["skills_dir"],
                provider_override=meta.get("provider_override"),
                review_id=review_id,
            )
            config = {"configurable": {"thread_id": review_id}}
            snapshot = app.get_state(config)
            if not snapshot.values:
                results.append({"review_id": review_id, "status": "no_checkpoint"})
                continue
            if snapshot.tasks and any(t.interrupts for t in snapshot.tasks):
                results.append({"review_id": review_id, "status": "awaiting_human"})
                continue
            final = app.invoke(None, config=config)
            results.append(
                {
                    "review_id": review_id,
                    "status": "recovered",
                    "verdict": (final.get("leader") or {}).get("verdict"),
                }
            )
        except Exception as exc:  # noqa: BLE001 — one broken review never blocks the rest
            results.append(
                {"review_id": review_id, "status": "error", "detail": str(exc)[:200]}
            )
    return results


def resume_review(
    review_id: str, decision: str, *, repo_dir: str = "."
) -> tuple[LeaderResult | None, ReviewState]:
    """Continue a review paused at Gate 3 from its SQLite checkpoint."""
    meta_path = Path(repo_dir) / ".mas" / "reviews" / review_id / "meta.yaml"
    if not meta_path.exists():
        raise FileNotFoundError(f"no paused review {review_id!r} under {repo_dir}")
    meta = yaml_lib.safe_load(meta_path.read_text(encoding="utf-8"))
    app, _ = build_graph(
        repo_dir=meta["repo_dir"],
        skills_dir=meta["skills_dir"],
        provider_override=meta.get("provider_override"),
        review_id=review_id,
    )
    final = app.invoke(
        Command(resume=decision), config={"configurable": {"thread_id": review_id}}
    )
    result = (
        LeaderResult.model_validate(final["leader"]) if final.get("leader") else None
    )
    return result, final
