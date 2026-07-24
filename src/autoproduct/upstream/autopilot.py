"""Autopilot (`autoproduct create`) — FDR in, working product out.

The non-technical flow. Gate philosophy: humans keep the judgments they
are BEST at (is this my intent? — asked in their own language); the
machine keeps the ones they cannot make (EARS validity, DAG soundness,
tests) — those auto-pass on their deterministic checks and every
auto-approval is recorded in the report, never silent.

Pipeline: assess FDR → (questions back to the user if not ready) →
discover → plain-language confirmation (unless --yes) → plan → for each
task in DAG order: spec → auto-approve if checks pass → build → review.
Output: product/BUILD-REPORT.md in the FDR's language.
"""

from __future__ import annotations

import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autoproduct.providers import get_provider
from autoproduct.upstream.discover import approve_brief, run_discovery
from autoproduct.upstream.fdr import Assessment, assess_fdr
from autoproduct.upstream.plan import approve_plan, load_plan, run_planning
from autoproduct.upstream.spec import approve_spec, run_spec_stage
from autoproduct.upstream.build import run_build
from autoproduct.yamlx import extract_mapping

REPORTER_MARKER = "plain-language reporter for non-technical founders"


class TaskOutcome(BaseModel):
    task_id: str
    title: str
    status: str  # built | spec_blocked | build_failed | error
    review_verdict: str | None = None
    detail: str = ""


class AutopilotResult(BaseModel):
    status: str  # needs_answers | awaiting_confirmation | completed | failed
    assessment: Assessment | None = None
    confirmation: str = ""
    outcomes: list[TaskOutcome] = Field(default_factory=list)
    report_path: str = ""
    auto_approvals: list[str] = Field(default_factory=list)


_CONFIRM_SYSTEM = f"""You are the {REPORTER_MARKER}. Restate the brief below
as a short confirmation the founder reads before the build starts — in the
SAME LANGUAGE as the FDR. Three sections, plain words, no tech terms:
1. 会做什么 / What will be built (from scope_now, as user-visible abilities)
2. 这次不做 / Not in this version (scope_later + scope_never)
3. 怎么算成功 / How we'll know it works
End with one line: reply `--yes` (or re-run with --yes) to start building.
Respond with the confirmation text only."""

_REPORT_SYSTEM = f"""You are the {REPORTER_MARKER}. Write BUILD-REPORT.md
for a non-technical founder, in the SAME LANGUAGE as the FDR: what was
built (per task, as user-visible abilities), what the automated reviewers
flagged (plain words: "needs attention" not verdict codes), and what the
founder should do next (try it, answer open questions). No jargon.
Respond with the markdown only."""


def run_autopilot(
    workspace: str | Path,
    fdr_path: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    yes: bool = False,
    max_tasks: int = 12,
    parallel: bool = False,
) -> AutopilotResult:
    root = Path(workspace).resolve()
    fdr_text = Path(fdr_path).read_text(encoding="utf-8")
    provider_impl = get_provider(provider)

    assessment = assess_fdr(fdr_text, provider=provider, model=model)
    if not assessment.ready:
        questions = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(assessment.questions))
        (root / "FDR-QUESTIONS.md").write_text(
            f"# 请回答这些问题 / Please answer these questions\n\n"
            f"{assessment.summary}\n\n{questions}\n\n"
            f"把答案补充进 FDR.md 后重新运行 `autoproduct create`。\n"
            f"Add your answers to FDR.md and run `autoproduct create` again.\n",
            encoding="utf-8",
        )
        return AutopilotResult(status="needs_answers", assessment=assessment)

    auto_approvals: list[str] = []
    brief = run_discovery(root, fdr_text, provider=provider)
    estimate = estimate_hint(root, len(brief.scope_now))
    confirmation = provider_impl.complete(
        model=model,
        system=_CONFIRM_SYSTEM,
        user=yaml.safe_dump(
            brief.model_dump(include={"title", "scope_now", "scope_later", "scope_never", "success_metrics"}),
            sort_keys=False, allow_unicode=True,
        )
        + f"\n预计/estimate: {estimate}\n"
        + f"\n<fdr_language_sample>\n{fdr_text[:400]}\n</fdr_language_sample>",
        max_tokens=1024,
    )
    confirmation += f"\n\n---\n{estimate}\n"
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "CONFIRMATION.md").write_text(confirmation, encoding="utf-8")
    if not yes:
        return AutopilotResult(
            status="awaiting_confirmation",
            assessment=assessment,
            confirmation=confirmation,
        )

    approve_brief(root)
    auto_approvals.append("Gate U1 (brief): confirmed via --yes on the plain-language summary")

    from autoproduct.upstream.provisioning import provision_local, write_cloud_guide
    from autoproduct.upstream.workspace import load_project as _lp

    provision_local(root)
    write_cloud_guide(root, _lp(root).profile)
    auto_approvals.append(
        "services: local SQLite provisioned (data/app.db); cloud options in SERVICES.md"
    )
    plan = run_planning(root, provider=provider)
    if plan.status == "blocked":
        return AutopilotResult(
            status="failed", assessment=assessment,
            confirmation=confirmation, auto_approvals=auto_approvals,
        )
    approve_plan(root)
    auto_approvals.append("Gate U2 (scope lock): auto — dag_check passed")

    outcomes: list[TaskOutcome] = []
    ordered = _topo_order(load_plan(root).tasks)[:max_tasks]
    if parallel:
        auto_approvals.append("parallel lanes: wave scheduling (one task per lane per wave)")
        for wave in schedule_waves(ordered):
            outcomes += _build_wave_parallel(
                root, wave, provider=provider, model=model, auto_approvals=auto_approvals
            )
        ordered = []
    for task in ordered:
        spec = run_spec_stage(
            root, f"{task.description} (task:{task.id})", provider=provider
        )
        if spec.status != "proposed":
            outcomes.append(
                TaskOutcome(task_id=task.id, title=task.title, status="spec_blocked",
                            detail=f"lint {len(spec.lint_issues)} issue(s)")
            )
            continue
        approve_spec(root, spec.slug)
        auto_approvals.append(
            f"Gate U3 ({spec.slug}): auto — ears_lint + coverage passed"
        )
        built = run_build(root, spec.slug, provider=provider, model=model,
                          task_lane=task.lane, task_estimate_hours=task.estimate_hours)
        verdict = None
        detail = built.detail
        if built.status == "built":
            review = _review_head(root, provider)
            verdict = review.verdict.value if review else None
            # Fix loop: critical/high findings get ONE bounded repair
            # iteration — recorded, re-reviewed, never silent.
            serious = [
                f for f in (review.findings if review else [])
                if f.severity.value in ("critical", "high")
            ]
            if serious and _fix_iteration(root, provider, model, serious):
                auto_approvals.append(
                    f"fix iteration ({spec.slug}): {len(serious)} serious review "
                    "finding(s) fed back to the implementer; suite re-passed"
                )
                re_review = _review_head(root, provider)
                if re_review:
                    verdict = re_review.verdict.value
                    detail = (detail + " " if detail else "") + "(after fix iteration)"
        outcomes.append(
            TaskOutcome(
                task_id=task.id, title=task.title,
                status=built.status, review_verdict=verdict, detail=detail,
            )
        )

    report = provider_impl.complete(
        model=model,
        system=_REPORT_SYSTEM,
        user=yaml.safe_dump(
            {
                "fdr_language_sample": fdr_text[:400],
                "brief_title": brief.title,
                "outcomes": [o.model_dump() for o in outcomes],
                "auto_approvals": auto_approvals,
            },
            sort_keys=False, allow_unicode=True,
        ),
        max_tokens=2048,
    )
    report_path = root / "product" / "BUILD-REPORT.md"
    report_path.write_text(report, encoding="utf-8")

    built_count = sum(1 for o in outcomes if o.status == "built")
    status = "completed" if built_count == len(outcomes) and outcomes else "failed"
    _post_build_artifacts(
        root, provider=provider, model=model, fdr_text=fdr_text,
        outcomes=outcomes, status=status,
    )
    return AutopilotResult(
        status=status,
        assessment=assessment,
        confirmation=confirmation,
        outcomes=outcomes,
        report_path=str(report_path),
        auto_approvals=auto_approvals,
    )


def estimate_hint(root: Path, item_count: int) -> str:
    """M7: honest expectation-setting from recorded actuals when they
    exist, defaults when they don't."""
    per_task_min = 10
    estimates_path = root / ".mas" / "estimates.yaml"
    if estimates_path.exists():
        history = yaml.safe_load(estimates_path.read_text(encoding="utf-8")) or []
        if len(history) >= 3:
            import statistics

            per_task_min = max(3, int(statistics.median(
                e["actual_seconds"] for e in history) / 60) or per_task_min)
    n = max(item_count, 1)
    return (
        f"预计约 {n}–{n + 3} 个模块，每个 {per_task_min}–{per_task_min * 3} 分钟，"
        f"部分模块可能失败并可单独重试。"
        f" / Roughly {n}–{n + 3} modules at {per_task_min}–{per_task_min * 3} min "
        "each; individual modules may fail and can be retried alone."
    )


def _post_build_artifacts(
    root: Path, *, provider: str, model: str, fdr_text: str, outcomes, status: str
) -> None:
    """M2/M4/M5/M7 wiring: outcomes record (retry UX), screenshots,
    acceptance walkthrough, telemetry module, undo checkpoint."""
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "outcomes.yaml").write_text(
        yaml.safe_dump([o.model_dump() for o in outcomes], sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    try:
        from autoproduct.upstream.telemetry import install_telemetry
        from autoproduct.upstream.workspace import load_project

        profile = load_project(root).profile
        install_telemetry(root, profile)
        from autoproduct.upstream.walkthrough import generate_walkthrough

        generate_walkthrough(
            root, provider=provider, model=model, language_sample=fdr_text
        )
        from autoproduct.upstream.screenshots import capture

        shots = capture(root, profile)
        if shots.captured or shots.note:
            (root / "product" / "screenshots.yaml").write_text(
                yaml.safe_dump(shots.model_dump(), sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
    except Exception:  # noqa: BLE001 — artifacts never fail the build
        pass
    if status == "completed":
        tag_checkpoint(root)


def tag_checkpoint(root: Path) -> str:
    """M7 undo: every completed build/feature gets a checkpoint tag."""
    import subprocess

    existing = subprocess.run(
        ["git", "tag", "--list", "ap-checkpoint-*"], cwd=root,
        capture_output=True, text=True,
    ).stdout.split()
    name = f"ap-checkpoint-{len(existing) + 1:03d}"
    subprocess.run(["git", "tag", name], cwd=root, capture_output=True)
    return name


def undo_last(root: Path) -> dict:
    """M7: 回到上一个版本 — resets to the previous checkpoint after saving
    a rescue branch, so even undo is undoable."""
    import subprocess
    import time as _time

    tags = subprocess.run(
        ["git", "tag", "--list", "ap-checkpoint-*", "--sort=version:refname"],
        cwd=root, capture_output=True, text=True,
    ).stdout.split()
    if len(tags) < 2:
        return {"status": "nothing_to_undo",
                "detail": "只有一个版本，暂时没有可回退的更早版本。"}
    rescue = f"rescue/{int(_time.time())}"
    subprocess.run(["git", "branch", rescue], cwd=root, capture_output=True)
    target = tags[-2]
    reset = subprocess.run(
        ["git", "reset", "--hard", target], cwd=root, capture_output=True, text=True
    )
    if reset.returncode != 0:
        return {"status": "error", "detail": reset.stderr[:200]}
    subprocess.run(["git", "tag", "-d", tags[-1]], cwd=root, capture_output=True)
    return {"status": "undone", "restored_to": target, "rescue_branch": rescue}


def _review_head(root: Path, provider: str):
    from autoproduct.orchestrator import run_review

    skills = Path(__file__).resolve().parent.parent.parent.parent / "skills"
    review, _ = run_review(
        # Committed range only — the working tree carries uncommitted
        # bookkeeping from later tasks mid-autopilot (Gate 2 apply
        # conflicts otherwise; found by the product bench).
        "HEAD~1..HEAD", repo_dir=str(root), skills_dir=str(skills),
        provider_override=provider if provider == "mock" else None,
    )
    return review


def _fix_iteration(root: Path, provider: str, model: str, findings) -> bool:
    """One bounded repair pass: findings → implementer → suite must pass →
    commit. Returns True when a fix commit landed."""
    import subprocess

    from autoproduct.testing import _pytest_in_subprocess
    from autoproduct.upstream.build import IMPLEMENTER_MARKER  # noqa: F401
    from autoproduct.upstream.build import _write_files

    listing = yaml.safe_dump(
        [
            {"file": f.file_path, "line": f.line_start, "severity": f.severity.value,
             "title": f.title, "explanation": f.explanation[:300]}
            for f in findings[:8]
        ],
        sort_keys=False, allow_unicode=True,
    )
    sources = {}
    for f in findings[:8]:
        path = root / f.file_path
        if path.is_file() and f.file_path not in sources:
            sources[f.file_path] = path.read_text(encoding="utf-8", errors="replace")
    file_blocks = "\n\n".join(
        f"<file path=\"{p}\">\n{t}\n</file>" for p, t in sources.items()
    )
    raw = get_provider(provider).complete(
        model=model,
        system=f"You are the {IMPLEMENTER_MARKER}. Fix ONLY the review "
        "findings below in the provided files — smallest change, complete "
        "file contents back, no drive-by edits.\n\nRespond with ONLY YAML:\n"
        "files:\n  - path: ...\n    new_content: |\n      ...",
        user=f"<review_findings>\n{listing}</review_findings>\n\n{file_blocks}",
        max_tokens=16384,
    )
    try:
        data = extract_mapping(raw, ("files",))
        written = _write_files(root, data.get("files") or [])
    except ValueError:
        return False
    if not written:
        return False
    if _pytest_in_subprocess(root).status not in ("passed", "no_tests"):
        subprocess.run(["git", "checkout", "--", "."], cwd=root, capture_output=True)
        return False
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    committed = subprocess.run(
        ["git", "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
         "commit", "-qm", "fix: address serious review findings"],
        cwd=root, capture_output=True, text=True,
    )
    return committed.returncode == 0


from autoproduct.upstream.plan import PLANNER_MARKER as _PLANNER_MARKER

_FEATURE_PLANNER_SYSTEM = f"""You are the {_PLANNER_MARKER},
planning ONE FEATURE CHANGE against an existing product.

Rules:
- 1-6 tasks; each is one spec+build cycle. Small features are ONE task.
- You see the existing file tree and prior features: plan integration with
  what exists — never plan rebuilding existing surfaces.
- depends_on only within this feature's tasks; no cycles.

Respond with ONLY YAML:
tasks:
  - id: f1
    title: ...
    description: one sentence, phrased as a spec request
    depends_on: []
    lane: api
    estimate_hours: 3
"""


def run_feature(
    workspace: str | Path,
    fdr_path: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    yes: bool = False,
) -> AutopilotResult:
    """Per-feature FDR on an existing product (granularity contract: one
    FDR = one feature change). Same gates, feature-scoped artifacts under
    product/features/."""
    from autoproduct.upstream.build import _file_tree
    from autoproduct.upstream.plan import Task, dag_check

    root = Path(workspace).resolve()
    fdr_text = Path(fdr_path).read_text(encoding="utf-8")
    provider_impl = get_provider(provider)

    assessment = assess_fdr(fdr_text, provider=provider, model=model)
    if not assessment.ready:
        questions = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(assessment.questions))
        (root / "FDR-QUESTIONS.md").write_text(
            f"# 请回答这些问题 / Please answer\n\n{assessment.summary}\n\n{questions}\n",
            encoding="utf-8",
        )
        return AutopilotResult(status="needs_answers", assessment=assessment)

    features_dir = root / "product" / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    # Idempotent per FDR: an unbuilt feature dir with identical FDR content
    # is resumed (confirm → build is two calls on the same feature).
    feature_dir = None
    for existing in sorted(features_dir.iterdir()):
        fdr_file = existing / "fdr.md"
        if (
            fdr_file.exists()
            and fdr_file.read_text(encoding="utf-8") == fdr_text
            and not (existing / "REPORT.md").exists()
        ):
            feature_dir = existing
            break
    if feature_dir is None:
        slug = f"{len(list(features_dir.iterdir())) + 1:02d}-" + "".join(
            c if c.isalnum() else "-" for c in assessment.summary[:32].lower()
        ).strip("-")
        feature_dir = features_dir / slug
        feature_dir.mkdir(exist_ok=True)
    slug = feature_dir.name
    (feature_dir / "fdr.md").write_text(fdr_text, encoding="utf-8")

    prior = "\n".join(f"- {d.name}" for d in sorted(features_dir.iterdir()) if d != feature_dir)
    from autoproduct.upstream.plan import blast_radius

    radius = blast_radius(root, fdr_text)
    from autoproduct.upstream.blocks import catalog_summary
    from autoproduct.upstream.workspace import load_project as _lp2

    blocks_note = catalog_summary(_lp2(root).profile)
    raw = provider_impl.complete(
        model=model,
        system=_FEATURE_PLANNER_SYSTEM,
        user=(f"<blocks>\n{blocks_note}\n</blocks>\n\n" if blocks_note else "")
        + f"<existing_tree>\n{_file_tree(root)}\n</existing_tree>\n\n"
        f"<likely_touched_files>\n"
        + ("\n".join(f"- {p}" for p in radius) or "(none matched)")
        + "\n</likely_touched_files>\n\n"
        f"<prior_features>\n{prior or '(first feature)'}\n</prior_features>\n\n"
        f"<feature_fdr>\n{fdr_text}\n</feature_fdr>",
        max_tokens=2048,
    )
    data = extract_mapping(raw, ("tasks",))
    tasks = [Task.model_validate(t) for t in data.get("tasks", [])]
    for task in tasks:
        if not task.files_expected:
            task.files_expected = blast_radius(
                root, f"{task.title} {task.description}", cap=3
            )
    issues = dag_check(tasks)
    if issues:
        return AutopilotResult(status="failed", assessment=assessment,
                               confirmation=f"plan dag_check failed: {issues}")
    (feature_dir / "plan.yaml").write_text(
        yaml.safe_dump([t.model_dump() for t in tasks], sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    confirmation = provider_impl.complete(
        model=model,
        system=_CONFIRM_SYSTEM,
        user=yaml.safe_dump(
            {"feature": assessment.summary,
             "tasks": [t.title for t in tasks]},
            sort_keys=False, allow_unicode=True,
        )
        + f"\n<fdr_language_sample>\n{fdr_text[:400]}\n</fdr_language_sample>",
        max_tokens=1024,
    )
    (feature_dir / "CONFIRMATION.md").write_text(confirmation, encoding="utf-8")
    if not yes:
        return AutopilotResult(
            status="awaiting_confirmation", assessment=assessment, confirmation=confirmation
        )

    auto_approvals = [f"feature plan ({slug}): auto — dag_check passed"]
    outcomes: list[TaskOutcome] = []
    for task in _topo_order(tasks):
        spec = run_spec_stage(
            root, f"{task.description} (task:{slug}-{task.id})", provider=provider
        )
        if spec.status != "proposed":
            outcomes.append(TaskOutcome(task_id=task.id, title=task.title,
                                        status="spec_blocked"))
            continue
        approve_spec(root, spec.slug)
        auto_approvals.append(f"Gate U3 ({spec.slug}): auto — ears_lint + coverage passed")
        built = run_build(root, spec.slug, provider=provider, model=model,
                          task_lane=task.lane, task_estimate_hours=task.estimate_hours)
        verdict = None
        if built.status == "built":
            review = _review_head(root, provider)
            verdict = review.verdict.value if review else None
            serious = [f for f in (review.findings if review else [])
                       if f.severity.value in ("critical", "high")]
            if serious and _fix_iteration(root, provider, model, serious):
                auto_approvals.append(
                    f"fix iteration ({spec.slug}): {len(serious)} finding(s) repaired"
                )
                re_review = _review_head(root, provider)
                if re_review:
                    verdict = re_review.verdict.value
        outcomes.append(TaskOutcome(task_id=task.id, title=task.title,
                                    status=built.status, review_verdict=verdict,
                                    detail=built.detail))

    report = provider_impl.complete(
        model=model,
        system=_REPORT_SYSTEM,
        user=yaml.safe_dump(
            {"fdr_language_sample": fdr_text[:400], "feature": slug,
             "outcomes": [o.model_dump() for o in outcomes],
             "auto_approvals": auto_approvals},
            sort_keys=False, allow_unicode=True,
        ),
        max_tokens=2048,
    )
    (feature_dir / "REPORT.md").write_text(report, encoding="utf-8")
    built_count = sum(1 for o in outcomes if o.status == "built")
    if outcomes and built_count == len(outcomes):
        tag_checkpoint(root)
        try:
            from autoproduct.upstream.walkthrough import generate_walkthrough

            generate_walkthrough(root, provider=provider, model=model,
                                 language_sample=fdr_text)
        except Exception:  # noqa: BLE001
            pass
    return AutopilotResult(
        status="completed" if outcomes and built_count == len(outcomes) else "failed",
        assessment=assessment, confirmation=confirmation, outcomes=outcomes,
        report_path=str(feature_dir / "REPORT.md"), auto_approvals=auto_approvals,
    )


def schedule_waves(tasks) -> list[list]:
    """Dependency waves with at most ONE task per lane per wave — lane_check
    guarantees cross-lane tasks don't share files, so a wave's builds can
    run in parallel worktrees and merge cleanly."""
    done: set[str] = set()
    remaining = list(tasks)
    waves: list[list] = []
    while remaining:
        ready = [t for t in remaining if set(t.depends_on) <= done]
        if not ready:
            break  # cycle — dag_check blocks these upstream
        wave, lanes_used = [], set()
        for task in ready:
            if task.lane in lanes_used:
                continue
            wave.append(task)
            lanes_used.add(task.lane)
        for task in wave:
            done.add(task.id)
            remaining.remove(task)
        waves.append(wave)
    return waves


def _build_wave_parallel(root, wave, *, provider, model, auto_approvals):
    """Each task of the wave builds in its own worktree branch; merges are
    applied serially afterwards; bookkeeping runs post-merge."""
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    from autoproduct.upstream.build import finalize_build_bookkeeping

    def build_one(item):
        task, spec_slug = item
        return task, run_build(
            root, spec_slug, provider=provider, model=model, in_branch=True,
            task_lane=task.lane, task_estimate_hours=task.estimate_hours,
        )

    prepared = []
    outcomes = []
    for task in wave:
        spec = run_spec_stage(root, f"{task.description} (task:{task.id})", provider=provider)
        if spec.status != "proposed":
            outcomes.append(
                TaskOutcome(task_id=task.id, title=task.title, status="spec_blocked")
            )
            continue
        approve_spec(root, spec.slug)
        auto_approvals.append(f"Gate U3 ({spec.slug}): auto — ears_lint + coverage passed")
        # Commit the spec in main BEFORE branching: lane worktrees see it in
        # HEAD, and the merge back can't collide with untracked spec files.
        subprocess.run(["git", "add", f"specs/{spec.slug}"], cwd=root, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
             "commit", "-qm", f"spec({spec.slug}): approved"],
            cwd=root, capture_output=True,
        )
        prepared.append((task, spec.slug))

    with ThreadPoolExecutor(max_workers=max(1, len(prepared))) as pool:
        built = list(pool.map(build_one, prepared))

    for task, result in built:
        if result.status != "built":
            outcomes.append(
                TaskOutcome(task_id=task.id, title=task.title,
                            status=result.status, detail=result.detail)
            )
            continue
        merged = subprocess.run(
            ["git", "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
             "merge", "--no-ff", "-m", f"merge build/{result.slug}", f"build/{result.slug}"],
            cwd=root, capture_output=True, text=True,
        )
        subprocess.run(["git", "branch", "-D", f"build/{result.slug}"],
                       cwd=root, capture_output=True)
        if merged.returncode != 0:
            subprocess.run(["git", "merge", "--abort"], cwd=root, capture_output=True)
            outcomes.append(
                TaskOutcome(task_id=task.id, title=task.title, status="merge_conflict",
                            detail=merged.stderr[:200] or merged.stdout[:200])
            )
            continue
        finalize_build_bookkeeping(root, result.slug, result.files_written)
        outcomes.append(
            TaskOutcome(task_id=task.id, title=task.title, status="built",
                        detail=f"parallel lane {task.lane}")
        )
    return outcomes


def _topo_order(tasks):
    done: set[str] = set()
    ordered = []
    remaining = list(tasks)
    while remaining:
        ready = [t for t in remaining if set(t.depends_on) <= done]
        if not ready:
            break  # cycle — dag_check should have blocked upstream
        for task in ready:
            ordered.append(task)
            done.add(task.id)
            remaining.remove(task)
    return ordered
