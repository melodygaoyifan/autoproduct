import shutil

import pytest
import yaml

from autoproduct.upstream import (
    approve_brief,
    approve_plan,
    init_workspace,
    next_tasks,
    run_discovery,
    run_planning,
)
from autoproduct.upstream.plan import Task, dag_check

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


def _task(tid, deps=(), lane="api"):
    return Task(id=tid, title=tid, depends_on=list(deps), lane=lane, estimate_hours=2)


def test_dag_check_accepts_valid_dag():
    assert dag_check([_task("a"), _task("b", ["a"]), _task("c", ["a", "b"])]) == []


def test_dag_check_catches_cycle():
    issues = dag_check([_task("a", ["b"]), _task("b", ["a"])])
    assert any("cycle" in i for i in issues)


def test_dag_check_catches_unknown_and_duplicate():
    issues = dag_check([_task("b"), _task("b"), _task("c", ["ghost"])])
    text = " | ".join(issues)
    assert "unknown" in text and "duplicate" in text


def test_discovery_writes_brief_and_ledger(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    brief = run_discovery(root, "a link sharing tool", provider="mock")
    assert brief.status == "proposed"
    assert {h.evidence for h in brief.hypotheses} <= {"measured", "sourced", "assumed"}
    ledger = yaml.safe_load((root / ".mas" / "hypotheses.yaml").read_text())
    assert len(ledger) == len(brief.hypotheses)
    assert all(e["verified"] is None for e in ledger)


def test_planning_requires_gate_u1(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    run_discovery(root, "a link sharing tool", provider="mock")
    with pytest.raises(ValueError, match="Gate U1"):
        run_planning(root, provider="mock")


def test_planner_cycle_forces_revision(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    run_discovery(root, "a link sharing tool, make a cycle", provider="mock")
    approve_brief(root)
    plan = run_planning(root, provider="mock")
    # Mock planner emits a t1<->t2 cycle on the first pass; dag_check
    # feedback forces the clean second pass.
    assert plan.revisions >= 1
    assert plan.status == "proposed"
    assert plan.dag_issues == []


def test_scope_lock_and_ready_queue(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    run_discovery(root, "a link sharing tool", provider="mock")
    approve_brief(root)
    run_planning(root, provider="mock")
    locked = approve_plan(root)
    assert locked.status == "locked"
    ready = next_tasks(root)
    assert [t.id for t in ready] == ["t1"]  # only the task with no deps
