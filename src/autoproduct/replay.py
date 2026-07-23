"""Replay a past review from its YAML mirror (§09.9).

The mirror is the human-readable audit trail; replay turns one review's
directory back into a timeline without touching the checkpointer.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ReplayStep:
    step: int
    node: str
    written_at: datetime.datetime
    payload: dict


@dataclass
class Replay:
    review_id: str
    steps: list[ReplayStep]

    @property
    def verdict(self) -> str | None:
        for step in reversed(self.steps):
            if "verdict" in step.payload:
                return step.payload["verdict"]
            if isinstance(step.payload.get("leader"), dict):
                return step.payload["leader"].get("verdict")
        return None

    @property
    def duration_s(self) -> float | None:
        if len(self.steps) < 2:
            return None
        return (self.steps[-1].written_at - self.steps[0].written_at).total_seconds()


def load_replay(reviews_dir: str | Path, review_id: str) -> Replay:
    review_dir = Path(reviews_dir) / review_id
    if not review_dir.is_dir():
        raise FileNotFoundError(f"no review {review_id!r} under {reviews_dir}")
    steps = []
    for path in sorted(review_dir.glob("[0-9][0-9]-*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        steps.append(
            ReplayStep(
                step=int(data.get("step", 0)),
                node=str(data.get("node", path.stem.split("-", 1)[-1])),
                written_at=datetime.datetime.fromisoformat(str(data.get("written_at"))),
                payload={
                    k: v for k, v in data.items() if k not in ("step", "node", "written_at")
                },
            )
        )
    if not steps:
        raise FileNotFoundError(f"review {review_id!r} has no mirror steps")
    return Replay(review_id=review_id, steps=steps)


def summarize_step(step: ReplayStep) -> str:
    payload = step.payload
    if step.node == "dor_gate":
        return "passed" if payload.get("dor_pass") else f"failed: {payload.get('dor_reasons')}"
    if step.node == "analyze":
        return f"mode={payload.get('mode')}"
    if step.node == "tools":
        reports = payload.get("tool_reports", [])
        ran = sum(1 for r in reports if r.get("status") == "ok")
        found = sum(len(r.get("findings", [])) for r in reports)
        return f"{ran}/{len(reports)} tools ran, {found} finding(s)"
    if step.node == "vote":
        outputs = payload.get("voter_outputs", [])
        blocked = sum(1 for o in outputs if o.get("status") != "OK")
        found = sum(len(o.get("findings", [])) for o in outputs)
        return f"{len(outputs)} voter(s), {found} finding(s), {blocked} blocked"
    if step.node == "verify":
        outputs = payload.get("verified_outputs", [])
        verdicts = [
            f.get("verification")
            for o in outputs
            for f in o.get("findings", [])
            if f.get("verification")
        ]
        confirmed = sum(1 for v in verdicts if v == "VERIFIED")
        return f"{confirmed}/{len(verdicts)} verified" if verdicts else "nothing to verify"
    if step.node == "leader":
        leader = payload.get("leader", {})
        return f"{leader.get('verdict')} ({len(leader.get('findings', []))} finding(s))"
    if step.node == "final":
        return str(payload.get("verdict"))
    return ", ".join(sorted(payload)) or "(empty)"
