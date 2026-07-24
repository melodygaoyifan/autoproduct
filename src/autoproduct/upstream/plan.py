"""Planning stage (§13.27) — the task DAG and Gate U2's scope lock.

The Planner turns an approved brief into tasks with dependencies and
lanes. `dag_check` is deterministic: unique ids, dependencies that exist,
no cycles — ~42% of MAS failures enter at specification/planning (MAST),
and a cyclic or dangling plan is the cheapest possible catch.

Gate U2 (`plan-approve`) locks scope: after the lock, the only legal way
to change the plan is a Spec Change Request — silent drift is the failure
mode the SCR back-edge exists to prevent (ADR-U02).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autoproduct.providers import get_provider
from autoproduct.upstream.discover import load_brief
from autoproduct.upstream.workspace import load_project
from autoproduct.yamlx import extract_mapping

PLANNER_MARKER = "task planner in a greenfield product system"
PLAN_CRITIC_MARKER = "plan critic panel"

MAX_REVISIONS = 2


class Task(BaseModel):
    id: str
    title: str
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)
    lane: str = "core"
    estimate_hours: float = Field(gt=0, le=40)
    files_expected: list[str] = Field(
        default_factory=list,
        description="globs the task expects to touch — lane_check input",
    )


class Plan(BaseModel):
    status: str = "proposed"  # proposed | locked | blocked
    brief_title: str
    tasks: list[Task]
    dag_issues: list[str] = Field(default_factory=list)
    critic_issues: list[dict] = Field(default_factory=list)
    revisions: int = 0


def lane_check(tasks: list[Task]) -> list[str]:
    """§13: single-writer enforced AT PLAN TIME — two tasks in DIFFERENT
    lanes declaring the same expected file is a collision waiting to
    happen. Same-lane overlap is fine (lanes serialize)."""
    from fnmatch import fnmatch

    issues = []
    for i, a in enumerate(tasks):
        for b in tasks[i + 1 :]:
            if a.lane == b.lane:
                continue
            for ga in a.files_expected:
                for gb in b.files_expected:
                    if ga == gb or fnmatch(ga, gb) or fnmatch(gb, ga):
                        issues.append(
                            f"lane collision: {a.id} ({a.lane}) and {b.id} "
                            f"({b.lane}) both expect {ga!r}"
                        )
    return issues


def budget_check(tasks: list[Task], budget_hours: float) -> list[str]:
    total = sum(t.estimate_hours for t in tasks)
    if total > budget_hours:
        return [
            f"plan estimates {total:.0f}h, over the {budget_hours:.0f}h budget "
            "— cut scope or split the feature"
        ]
    return []


def record_actual(repo_dir: str | Path, lane: str, estimate_hours: float, actual_seconds: float) -> None:
    """estimate_calibrator's raw material: what builds actually cost."""
    path = Path(repo_dir) / ".mas" / "estimates.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    history = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    history = (history or [])[-200:]
    history.append(
        {"lane": lane, "estimate_hours": estimate_hours,
         "actual_seconds": round(actual_seconds, 1)}
    )
    path.write_text(yaml.safe_dump(history, sort_keys=False), encoding="utf-8")


def calibration_note(repo_dir: str | Path, tasks: list[Task]) -> str:
    """Advisory (n>=5 per doc 13): compare plan estimates against the
    recorded reality for the same lanes."""
    path = Path(repo_dir) / ".mas" / "estimates.yaml"
    if not path.exists():
        return ""
    history = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if len(history) < 5:
        return ""
    import statistics

    median_s = statistics.median(e["actual_seconds"] for e in history)
    return (
        f"calibration: {len(history)} recorded build(s), median actual "
        f"{median_s:.0f}s per task — treat hour-estimates as relative sizing"
    )


def blast_radius(repo_dir: str | Path, text: str, cap: int = 20) -> list[str]:
    """Files the change will plausibly touch — token overlap between the
    FDR/brief text and existing file paths/symbols. Advisory planner
    context (the full repo-graph version arrives with code_intel)."""
    from autoproduct.maintenance.correlate import _tokens

    tokens = _tokens(text)
    root = Path(repo_dir)
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".js", ".ts", ".wxml", ".json"):
            continue
        rel = path.relative_to(root)
        if any(part in (".git", ".mas", "node_modules", "__pycache__", "specs") for part in rel.parts):
            continue
        stem_tokens = _tokens(str(rel).replace("/", " ").replace("_", " ").replace(".", " "))
        if stem_tokens & tokens:
            hits.append(str(rel))
        if len(hits) >= cap:
            break
    return hits


def dag_check(tasks: list[Task]) -> list[str]:
    issues = []
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        issues.append("duplicate task ids")
    known = set(ids)
    for task in tasks:
        for dep in task.depends_on:
            if dep not in known:
                issues.append(f"task {task.id!r} depends on unknown {dep!r}")
            if dep == task.id:
                issues.append(f"task {task.id!r} depends on itself")
    # Kahn's algorithm: anything left over sits on a cycle.
    remaining = {t.id: set(t.depends_on) & known for t in tasks}
    while True:
        ready = [tid for tid, deps in remaining.items() if not deps]
        if not ready:
            break
        for tid in ready:
            remaining.pop(tid)
            for deps in remaining.values():
                deps.discard(tid)
    if remaining:
        issues.append(f"dependency cycle involving {sorted(remaining)}")
    return issues


_PLANNER_SYSTEM = f"""You are the {PLANNER_MARKER}. Decompose the approved
brief's scope_now into a task DAG a solo developer can execute.

Rules:
- 3-12 tasks; each is one `autoproduct spec` + `build` cycle (<= 2 days).
- depends_on lists task ids that must land first; no cycles.
- lane groups tasks that touch the same surface (e.g. api, ui, infra) —
  tasks in one lane run serially, lanes run in parallel.
- Only scope_now. scope_later items do NOT get tasks.
- Every task is a USER-VISIBLE feature slice specifiable as acceptance
  criteria. NO meta-tasks: no "test skeleton", "contract tests",
  "performance benchmark", "E2E suite", or "infrastructure" tasks — tests
  ship inside each feature task's own spec, never as a separate task.

Respond with ONLY YAML:
tasks:
  - id: t1
    title: ...
    description: one sentence, phrased as a spec request
    depends_on: []
    lane: api
    estimate_hours: 4
    files_expected: ["app/orders*.py"]   # globs this task will touch
"""

_CRITIC_SYSTEM = f"""You are the {PLAN_CRITIC_MARKER}: judge completeness
(does the DAG cover all of scope_now; is anything in it NOT in scope_now)
and dependency realism (missing edges that will bite mid-build; false edges
that serialize needlessly). Majors only where the plan would mislead
Gate U2.

Respond with ONLY YAML:
issues:
  - severity: major|minor
    lens: completeness|dependencies
    problem: one sentence
"""


def run_planning(
    repo_dir: str | Path,
    *,
    provider: str = "anthropic",
    planner_model: str = "claude-opus-4-8",
    critic_model: str = "claude-sonnet-5",
) -> Plan:
    load_project(repo_dir)
    brief = load_brief(repo_dir)
    if brief.status != "approved":
        raise ValueError(
            f"brief status is {brief.status!r} — Gate U1 requires "
            "`autoproduct brief-approve` before planning"
        )
    provider_impl = get_provider(provider)
    brief_yaml = yaml.safe_dump(
        brief.model_dump(include={"title", "problem", "scope_now", "success_metrics"}),
        sort_keys=False, allow_unicode=True,
    )

    feedback = ""
    tasks: list[Task] = []
    dag_issues: list[str] = []
    critics: list[dict] = []
    for revision in range(MAX_REVISIONS + 1):
        raw = provider_impl.complete(
            model=planner_model,
            system=_PLANNER_SYSTEM,
            user=f"<brief>\n{brief_yaml}</brief>"
            + (f"\n\n<revision_feedback>\n{feedback}\n</revision_feedback>" if feedback else ""),
            max_tokens=4096,
        )
        try:
            data = extract_mapping(raw, ("tasks",))
            tasks = [Task.model_validate(t) for t in data.get("tasks", [])]
        except Exception as exc:  # noqa: BLE001 — parse/schema failure feeds revision
            feedback = (
                f"Your previous response failed to parse ({type(exc).__name__}). "
                "Respond with ONLY the YAML schema given, double-quoting every "
                "string value."
            )
            tasks, dag_issues, critics = [], ["unparseable planner output"], []
            continue
        for task in tasks:
            if not task.files_expected:
                # lane_check only bites when globs exist; derive a fallback
                # from blast radius when the planner omits them.
                task.files_expected = blast_radius(
                    repo_dir, f"{task.title} {task.description}", cap=3
                )
        budget = float(
            yaml.safe_load(
                (Path(repo_dir) / ".mas" / "project.yaml").read_text(encoding="utf-8")
            ).get("budget_hours", 60)
        )
        dag_issues = dag_check(tasks) + lane_check(tasks) + budget_check(tasks, budget)
        raw_critique = provider_impl.complete(
            model=critic_model,
            system=_CRITIC_SYSTEM,
            user=f"<brief>\n{brief_yaml}</brief>\n\n<tasks>\n"
            + yaml.safe_dump([t.model_dump() for t in tasks], sort_keys=False, allow_unicode=True)
            + "</tasks>",
            max_tokens=1024,
        )
        try:
            critics = [
                i for i in (extract_mapping(raw_critique, ("issues",)).get("issues") or [])
                if isinstance(i, dict)
            ][:10]
        except ValueError:
            critics = []
        majors = [c for c in critics if c.get("severity") == "major"]
        if not dag_issues and not majors:
            break
        feedback = yaml.safe_dump(
            {"dag_issues": dag_issues, "critic_majors": majors},
            sort_keys=False, allow_unicode=True,
        )

    plan = Plan(
        status="proposed" if not dag_issues else "blocked",
        brief_title=brief.title,
        tasks=tasks,
        dag_issues=dag_issues,
        critic_issues=critics,
        revisions=revision,
    )
    note = calibration_note(repo_dir, tasks)
    if note:
        plan.critic_issues.append({"severity": "minor", "lens": "estimates", "problem": note})
    _save(repo_dir, plan)
    return plan


def _save(repo_dir: str | Path, plan: Plan) -> None:
    directory = Path(repo_dir) / "product"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plan.yaml").write_text(
        yaml.safe_dump(plan.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    rows = "\n".join(
        f"| {t.id} | {t.title} | {', '.join(t.depends_on) or '—'} | {t.lane} | {t.estimate_hours}h |"
        for t in plan.tasks
    )
    (directory / "plan.md").write_text(
        f"# Plan — {plan.brief_title}\n\nstatus: **{plan.status}** · "
        f"{len(plan.tasks)} task(s) · revisions: {plan.revisions}\n\n"
        f"| id | task | depends on | lane | est |\n|---|---|---|---|---|\n{rows}\n\n"
        f"Lock scope with: `autoproduct plan-approve` (Gate U2). After the "
        f"lock, scope changes go through an SCR, never silently.\n",
        encoding="utf-8",
    )


def load_plan(repo_dir: str | Path) -> Plan:
    path = Path(repo_dir) / "product" / "plan.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no plan under {repo_dir}/product (run `autoproduct plan`)")
    return Plan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def approve_plan(repo_dir: str | Path) -> Plan:
    """Gate U2 — scope lock."""
    plan = load_plan(repo_dir)
    if plan.status == "blocked":
        raise ValueError(f"plan is blocked by dag_check: {plan.dag_issues}")
    plan.status = "locked"
    _save(repo_dir, plan)
    return plan


def next_tasks(repo_dir: str | Path) -> list[Task]:
    """Tasks whose dependencies are all spec'd+built (tracked by specs/ dirs
    with a built marker) — the work queue view."""
    plan = load_plan(repo_dir)
    done = set()
    specs_dir = Path(repo_dir) / "specs"
    for spec_dir in specs_dir.glob("*/spec.yaml"):
        data = yaml.safe_load(spec_dir.read_text(encoding="utf-8")) or {}
        if data.get("task_id") and data.get("built"):
            done.add(data["task_id"])
    return [
        t for t in plan.tasks
        if t.id not in done and all(d in done for d in t.depends_on)
    ]
