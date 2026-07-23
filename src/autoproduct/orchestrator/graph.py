"""The review state machine (§09.5).

Deterministic control flow, probabilistic analysis (Principle 1): this
module decides what runs next; LLMs only ever run inside voter nodes.

Current graph:

    dor_gate -> init -> analyze -> vote -> verify -> leader -> post
        \\-(not ready)-> post

tools / peer / adversarial_test nodes land in later milestones and slot
between analyze and post without changing this topology's contract.
"""

from __future__ import annotations

import functools
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from autoproduct import leader as leader_mod
from autoproduct import scoring, verify
from autoproduct.diff import ParsedDiff, fetch_diff, parse_unified_diff
from autoproduct.mirror import YamlMirror
from autoproduct.orchestrator.mode_router import select_mode
from autoproduct.state import LeaderResult, ReviewState, VoterFinding, VoterOutput
from autoproduct.voters import load_voters

MAX_REVIEWABLE_LINES = 2000

# fast mode = the single cheap reviewer from §08.3.5 (style runs on Haiku).
FAST_MODE_ROSTER = {"style"}


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


def vote_node(
    state: ReviewState, *, skills_dir: str, provider_override: str | None
) -> dict[str, Any]:
    voters = load_voters(skills_dir, provider_override=provider_override)
    if state.get("mode") == "fast":
        fast = [v for v in voters if v.spec.name in FAST_MODE_ROSTER]
        voters = fast or voters[:1]
    diff_raw = state["diff"]["raw"]
    context = state.get("project_context", "")
    with ThreadPoolExecutor(max_workers=len(voters)) as pool:
        outputs = list(pool.map(lambda v: v.run(diff_raw, context=context), voters))
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

    todo = [f for o in outputs for f in o.findings]
    if todo:
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
            list(pool.map(check, todo))
        for finding in todo:
            finding.score = scoring.score_finding(finding, todo)
    return {"verified_outputs": [o.model_dump(mode="json") for o in outputs]}


def leader_node(state: ReviewState) -> dict[str, Any]:
    raw = state.get("verified_outputs") or state["voter_outputs"]
    outputs = [VoterOutput.model_validate(o) for o in raw]
    result = leader_mod.synthesize(outputs)
    return {"leader": result.model_dump(mode="json")}


def post_node(state: ReviewState, *, mirror: YamlMirror) -> dict[str, Any]:
    if not state.get("dor_pass"):
        mirror.write("dor_fail", {"reasons": state.get("dor_reasons", [])})
        return {"artifacts_dir": str(mirror.dir)}
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
        "vote",
        mirrored(
            "vote",
            functools.partial(
                vote_node, skills_dir=skills_dir, provider_override=provider_override
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
    graph.add_node("leader", mirrored("leader", leader_node))
    graph.add_node("post", functools.partial(post_node, mirror=mirror))

    graph.set_entry_point("dor_gate")
    graph.add_conditional_edges(
        "dor_gate", lambda s: "init" if s["dor_pass"] else "post"
    )
    graph.add_edge("init", "analyze")
    graph.add_edge("analyze", "vote")
    graph.add_edge("vote", "verify")
    graph.add_edge("verify", "leader")
    graph.add_edge("leader", "post")
    graph.add_edge("post", END)
    return graph.compile(), review_id


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
    initial: ReviewState = {
        "review_id": review_id,
        "target": target,
        "mode_override": mode_override,
    }
    if diff_text is not None:
        initial["diff"] = {"raw": diff_text}
    final = app.invoke(initial)
    result = (
        LeaderResult.model_validate(final["leader"]) if final.get("leader") else None
    )
    return result, final
