"""CLI entry point: `autoproduct review <target>`.

Target is a GitHub PR URL (requires `gh` auth) or a local git revision
range such as `main...HEAD`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from autoproduct.orchestrator import is_interrupted, resume_review, run_review
from autoproduct.state import Verdict

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()

_DEFAULT_SKILLS = Path(__file__).resolve().parent.parent.parent / "skills"


@app.callback()
def _root() -> None:
    """autoproduct — multi-agent review-side SDLC system."""


@app.command()
def review(
    target: str = typer.Argument(..., help="GitHub PR URL or git range (e.g. main...HEAD)"),
    repo_dir: str = typer.Option(".", help="Repository to review in"),
    skills_dir: str = typer.Option(str(_DEFAULT_SKILLS), help="Voter skills directory"),
    mode: str = typer.Option(None, help="Override mode: fast | standard | deep"),
    provider: str = typer.Option(
        None,
        help="Force one provider for all voters (e.g. 'mock' for offline runs; "
        "heterogeneity is the default posture)",
    ),
):
    result, state = run_review(
        target,
        repo_dir=repo_dir,
        skills_dir=skills_dir,
        provider_override=provider,
        mode_override=mode,
    )

    if not state.get("dor_pass"):
        console.print("[yellow]Not ready for review (Gate 1 failed):[/yellow]")
        for reason in state.get("dor_reasons", []):
            console.print(f"  - {reason}")
        raise typer.Exit(code=2)

    if is_interrupted(state):
        console.print(
            f"\n[bold red]{state['leader']['verdict']}[/bold red] — paused at "
            "Gate 3 (Review Gate) for human decision."
        )
        if state.get("hitl_issue_url"):
            console.print(f"Issue: {state['hitl_issue_url']}")
        elif state.get("hitl_note"):
            console.print(f"(no issue created: {state['hitl_note']})")
        console.print(
            f"Resume with: autoproduct resume {state['review_id']} "
            f"--decision ack   (or --decision override:REQUEST_CHANGES)"
        )
        raise typer.Exit(code=3)

    assert result is not None
    color = {
        Verdict.APPROVE: "green",
        Verdict.APPROVE_WITH_NOTES: "green",
        Verdict.REQUEST_CHANGES: "yellow",
    }.get(result.verdict, "red")
    console.print(
        f"\n[bold {color}]{result.verdict.value}[/bold {color}] — {result.summary}"
    )

    if result.findings:
        table = Table(show_lines=False)
        table.add_column("Sev")
        table.add_column("Location")
        table.add_column("Finding")
        for f in result.findings:
            table.add_row(
                f.severity.value,
                f"{f.file_path}:{f.line_start}",
                f"{f.title} [{f.voter}]",
            )
        console.print(table)

    console.print(f"\nArtifacts: {state['artifacts_dir']}")
    if result.verdict.is_escalation:
        raise typer.Exit(code=3)


@app.command()
def resume(
    review_id: str = typer.Argument(..., help="Review ID shown when the run paused"),
    decision: str = typer.Option(
        ..., help="'ack' to accept the verdict, or 'override:<VERDICT>'"
    ),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
):
    """Continue a review paused at Gate 3 (Review Gate)."""
    result, state = resume_review(review_id, decision, repo_dir=repo_dir)
    assert result is not None
    console.print(
        f"\nResumed with decision [bold]{decision}[/bold] → "
        f"[bold]{result.verdict.value}[/bold] — {result.summary}"
    )
    console.print(f"Artifacts: {state['artifacts_dir']}")


@app.command()
def replay(
    review_id: str = typer.Argument(None, help="Review ID; omit to list reviews"),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
):
    """Replay a past review's audit trail from its YAML mirror."""
    from autoproduct.replay import load_replay, summarize_step

    reviews_dir = Path(repo_dir) / ".mas" / "reviews"
    if review_id is None:
        rows = sorted(p.name for p in reviews_dir.iterdir() if p.is_dir())
        for name in rows:
            console.print(name)
        if not rows:
            console.print("(no reviews recorded)")
        return

    rep = load_replay(reviews_dir, review_id)
    table = Table(show_lines=False, title=f"review {rep.review_id}")
    table.add_column("#")
    table.add_column("node")
    table.add_column("at")
    table.add_column("summary")
    for step in rep.steps:
        table.add_row(
            str(step.step),
            step.node,
            step.written_at.strftime("%H:%M:%S"),
            summarize_step(step),
        )
    console.print(table)
    console.print(
        f"verdict: [bold]{rep.verdict}[/bold]"
        + (f" · {rep.duration_s:.1f}s" if rep.duration_s is not None else "")
    )


@app.command()
def compound(
    repo_dir: str = typer.Option(".", help="Repository whose review record to aggregate"),
    days: int = typer.Option(7, help="Signal window in days"),
    provider: str = typer.Option("anthropic", help="Proposer provider"),
    model: str = typer.Option("claude-opus-4-8", help="Proposer model"),
    pr: bool = typer.Option(
        False, "--pr", help="Open a CLAUDE.md update PR (human still merges)"
    ),
):
    """Weekly compounding loop: aggregate review signals, propose CLAUDE.md
    constraints, optionally open the human-gated update PR (§09.8)."""
    import datetime
    import subprocess

    from autoproduct import compound as comp

    date = datetime.date.today().isoformat()
    signals = comp.collect_signals(repo_dir, days=days)
    proposals = comp.propose(signals, provider=provider, model=model)
    report = comp.render_proposal(signals, proposals, date=date)

    out_dir = Path(repo_dir) / ".mas" / "compound"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"proposal-{date}.md"
    report_path.write_text(report, encoding="utf-8")
    console.print(report)
    console.print(f"\nProposal written to {report_path}")

    if not proposals:
        raise typer.Exit(code=0)
    if not pr:
        console.print("Re-run with --pr to open the CLAUDE.md update PR.")
        raise typer.Exit(code=0)

    branch = f"autoproduct/compound-{date}"
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=repo_dir, capture_output=True, text=True
        )

    git("checkout", "-B", branch)
    comp.apply_to_claude_md(repo_dir, proposals, date=date)
    git("add", "CLAUDE.md")
    git("commit", "-m", f"compound: propose {len(proposals)} CLAUDE.md constraint(s) ({date})")
    push = git("push", "-u", "origin", branch)
    if push.returncode != 0:
        console.print(f"[yellow]push failed: {push.stderr.strip()[:200]}[/yellow]")
        raise typer.Exit(code=1)
    created = subprocess.run(
        [
            "gh", "pr", "create",
            "--title", f"[compound] CLAUDE.md constraints — {date}",
            "--body", report + "\n\n🤖 opened by the autoproduct compounding loop",
        ],
        cwd=repo_dir, capture_output=True, text=True,
    )
    output = (created.stdout or created.stderr).strip()
    git("checkout", "-")
    if created.returncode != 0:
        console.print(f"[yellow]gh pr create failed: {output[:200]}[/yellow]")
        raise typer.Exit(code=1)
    console.print(output.splitlines()[-1] if output else "(no gh output)")


_DEFAULT_CASES = Path(__file__).resolve().parent.parent.parent / "benchmarks" / "cases"


@app.command()
def bench(
    cases_dir: str = typer.Option(str(_DEFAULT_CASES), help="Labeled benchmark cases"),
    skills_dir: str = typer.Option(str(_DEFAULT_SKILLS), help="Voter skills directory"),
    provider: str = typer.Option(None, help="Force one provider (e.g. 'mock')"),
    limit: int = typer.Option(None, help="Run only the first N cases"),
    repo_dir: str = typer.Option(".", help="Where to record the result"),
):
    """Run the labeled benchmark; v0.1.0 bars: recall >=40%, precision >=50%."""
    from autoproduct.bench import run_benchmark, save_result

    result = run_benchmark(
        cases_dir, skills_dir=skills_dir, provider_override=provider, limit=limit
    )
    table = Table(title="benchmark")
    for col in ("case", "verdict", "recall", "findings (matched)", "s"):
        table.add_column(col)
    for c in result.cases:
        table.add_row(
            c.name,
            c.verdict,
            f"{c.expected_matched}/{c.expected_total}",
            f"{c.findings_total} ({c.findings_matched})",
            str(c.duration_s),
        )
    console.print(table)
    verdict = "PASS" if result.passes() else "FAIL"
    console.print(
        f"recall [bold]{result.recall:.0%}[/bold] (bar 40%) · "
        f"precision [bold]{result.precision:.0%}[/bold] (bar 50%) → [bold]{verdict}[/bold]"
    )
    console.print(f"saved: {save_result(result, repo_dir)}")
    if not result.passes():
        raise typer.Exit(code=1)


_DEPLOY_SKILLS = Path(__file__).resolve().parent.parent.parent / "skills" / "deploy"


@app.command("deploy-review")
def deploy_review(
    target: str = typer.Argument(..., help="GitHub PR URL or git range"),
    repo_dir: str = typer.Option(".", help="Repository to review in"),
    skills_dir: str = typer.Option(str(_DEPLOY_SKILLS), help="Deploy voter skills"),
    provider: str = typer.Option(None, help="Force one provider (e.g. 'mock')"),
):
    """Gate 5 — Deployment Review MAS (§09.11). Recommends; never deploys."""
    from autoproduct.deploy import run_deploy_review

    result = run_deploy_review(
        target, repo_dir=repo_dir, skills_dir=skills_dir, provider_override=provider
    )
    color = "green" if result.verdict.value == "PROMOTE" else (
        "yellow" if result.verdict.value == "HOLD_FOR_HUMAN" else "red"
    )
    console.print(f"\n[bold {color}]{result.verdict.value}[/bold {color}] — {result.summary}")
    if result.findings:
        table = Table(show_lines=False)
        for col in ("Sev", "Location", "Finding"):
            table.add_column(col)
        for f in result.findings:
            table.add_row(
                f.severity.value, f"{f.file_path}:{f.line_start}", f"{f.title} [{f.voter}]"
            )
        console.print(table)
    console.print(f"Artifacts: {result.artifacts_dir}")
    if result.verdict.value.startswith("ESCALATE_"):
        raise typer.Exit(code=3)


@app.command()
def triage(
    incident_file: str = typer.Argument(..., help="Incident file (.json/.yaml/.txt)"),
    repo_dir: str = typer.Option(".", help="Repository to correlate against"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
    days: int = typer.Option(7, help="Correlation window for recent commits"),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Assistive tier: attempt a fix-PR when a root cause is proposed "
        "(this flag IS the human approval; the PR still re-enters code review)",
    ),
):
    """Gate 6 intake — Maintenance MAS (§09.12): triage + root-cause."""
    from autoproduct.maintenance import Incident, run_maintenance

    incident = Incident.load(incident_file)
    result = run_maintenance(
        incident, repo_dir=repo_dir, provider=provider, days=days
    )
    color = {
        "TRIAGED_LOW_PRIORITY": "green",
        "ROOT_CAUSE_PROPOSED": "yellow",
    }.get(result.verdict.value, "red")
    console.print(f"\n[bold {color}]{result.verdict.value}[/bold {color}] — {result.summary}")
    if result.root_cause:
        console.print(f"hypothesis: {result.root_cause.hypothesis}")
        console.print(f"next action: {result.root_cause.next_action}")
    if result.suspects:
        console.print("suspects: " + ", ".join(s["sha"] for s in result.suspects))
    console.print(f"Artifacts: {result.artifacts_dir}")

    if fix and result.verdict.value == "ROOT_CAUSE_PROPOSED":
        from autoproduct.maintenance.fixpr import generate_fix_pr

        attempt = generate_fix_pr(
            incident, result.root_cause, repo_dir=repo_dir, provider=provider
        )
        console.print(
            f"\nfix attempt: [bold]{attempt.status}[/bold]"
            + (f" · branch {attempt.branch}" if attempt.branch else "")
            + (f" · {attempt.pr_url}" if attempt.pr_url else "")
        )
        if attempt.detail:
            console.print(f"  {attempt.detail}")
        if attempt.files_changed:
            console.print(f"  files: {', '.join(attempt.files_changed)}")
    elif fix:
        console.print("\nfix attempt skipped: no root cause proposed")

    if result.verdict.value.startswith("ESCALATE_"):
        raise typer.Exit(code=3)


@app.command("deploy-outcome")
def deploy_outcome(
    review_id: str = typer.Argument(..., help="Deploy review ID"),
    outcome: str = typer.Option(..., help="'correct' or 'incorrect'"),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
):
    """Record the human verdict on a past deploy recommendation (§09.11.5).
    Streaks of correct PROMOTEs make the stage eligible for assistive tier."""
    from autoproduct.deploy import track_record

    if not track_record.mark_outcome(repo_dir, review_id, outcome):
        console.print(f"[red]no deploy review {review_id!r} on record[/red]")
        raise typer.Exit(code=1)
    ready = track_record.readiness(repo_dir)
    console.print(
        f"recorded. streak: {ready.streak}/{ready.needed} correct PROMOTEs"
        + (" — [bold]eligible for assistive tier[/bold]" if ready.eligible else "")
    )


@app.command()
def serve(
    repo_dir: str = typer.Option(".", help="Repository the server operates on"),
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(8422, help="Port"),
):
    """Webhook mode: GitHub PR events -> reviews, incident POSTs -> triage.
    Requires AUTOPRODUCT_WEBHOOK_SECRET for signature verification."""
    from autoproduct.server import serve as run_server

    run_server(repo_dir, host=host, port=port)


@app.command()
def init(
    directory: str = typer.Argument(..., help="Workspace directory to create"),
    name: str = typer.Option(None, help="Project name (defaults to directory name)"),
    profile: str = typer.Option(..., help="Domain profile: web | miniprogram | app"),
):
    """Create a greenfield workspace: profile constraints, CLAUDE.md, specs/."""
    from autoproduct.upstream import init_workspace

    root = init_workspace(directory, name or Path(directory).name, profile)
    console.print(f"workspace ready: {root}")
    console.print(
        f"next: autoproduct spec \"<what you want to build>\" --repo-dir {root}"
    )


@app.command()
def spec(
    request: str = typer.Argument(..., help="What you want to build, in plain words"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
):
    """Spec stage: EARS criteria + test skeletons, linted and critiqued."""
    from autoproduct.upstream import run_spec_stage

    result = run_spec_stage(repo_dir, request, provider=provider)
    color = {"proposed": "green", "blocked": "red"}.get(result.status, "yellow")
    console.print(
        f"\n[bold {color}]{result.status}[/bold {color}] — {result.title} "
        f"({len(result.criteria)} criteria, {result.revisions} revision(s))"
    )
    for i, criterion in enumerate(result.criteria):
        console.print(f"  {i}. {criterion}")
    if result.lint_issues:
        console.print(f"[red]lint issues: {result.lint_issues}[/red]")
    console.print(f"spec: {Path(repo_dir) / 'specs' / result.slug / 'spec.md'}")
    if result.status == "proposed":
        console.print(
            f"Gate U3: autoproduct spec-approve {result.slug} --repo-dir {repo_dir}"
        )


@app.command("spec-approve")
def spec_approve(
    slug: str = typer.Argument(..., help="Spec slug"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
):
    """Gate U3 — human approval that makes a spec buildable."""
    from autoproduct.upstream import approve_spec

    result = approve_spec(repo_dir, slug)
    console.print(
        f"approved: {result.title}\n"
        f"next: autoproduct build {slug} --repo-dir {repo_dir}"
    )


@app.command()
def build(
    slug: str = typer.Argument(..., help="Approved spec slug"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
    review: bool = typer.Option(
        True, help="Run the review pipeline on the built commit"
    ),
):
    """Coding stage: test-first implementation of an approved spec; the
    commit is handed to the review pipeline (Gate U4 -> Gate 1)."""
    from autoproduct.upstream import run_build

    result = run_build(repo_dir, slug, provider=provider)
    color = {"built": "green"}.get(result.status, "red")
    console.print(
        f"\n[bold {color}]{result.status}[/bold {color}] — {result.iterations} "
        f"iteration(s); {len(result.files_written)} file(s); {result.test_summary}"
    )
    if result.detail:
        console.print(result.detail)
    if result.status != "built":
        raise typer.Exit(code=1)
    console.print(f"commit {result.commit}: {', '.join(result.files_written)}")
    if review:
        console.print("\nhanding to review stage (autoproduct review HEAD~1)…")
        review_result, state = run_review(
            "HEAD~1..HEAD",
            repo_dir=repo_dir,
            skills_dir=str(_DEFAULT_SKILLS),
            provider_override=provider if provider == "mock" else None,
        )
        if review_result:
            console.print(
                f"review verdict: [bold]{review_result.verdict.value}[/bold] — "
                f"{review_result.summary}"
            )


@app.command()
def discover(
    idea: str = typer.Argument(..., help="Your product idea, in plain words"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
):
    """Discovery stage: evidence-tagged ProductBrief + hypothesis ledger."""
    from autoproduct.upstream import run_discovery

    brief = run_discovery(repo_dir, idea, provider=provider)
    console.print(f"\n[bold]{brief.title}[/bold] — {brief.status}")
    for h in brief.hypotheses:
        console.print(f"  ({h.evidence}) {h.statement}")
    console.print(f"scope_now: {brief.scope_now}")
    console.print(f"brief: {Path(repo_dir) / 'product' / 'brief.md'}")
    console.print("Gate U1: autoproduct brief-approve")


@app.command("brief-approve")
def brief_approve(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """Gate U1 — the human problem-selection decision."""
    from autoproduct.upstream import approve_brief

    brief = approve_brief(repo_dir)
    console.print(f"approved: {brief.title}\nnext: autoproduct plan")


@app.command()
def plan(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
):
    """Planning stage: task DAG from the approved brief (dag-checked)."""
    from autoproduct.upstream import run_planning

    result = run_planning(repo_dir, provider=provider)
    color = {"proposed": "green", "blocked": "red"}.get(result.status, "yellow")
    console.print(f"\n[bold {color}]{result.status}[/bold {color}] — {len(result.tasks)} task(s)")
    for t in result.tasks:
        deps = f" <- {','.join(t.depends_on)}" if t.depends_on else ""
        console.print(f"  {t.id} [{t.lane}] {t.title}{deps} ({t.estimate_hours}h)")
    if result.dag_issues:
        console.print(f"[red]dag issues: {result.dag_issues}[/red]")
    if result.status == "proposed":
        console.print("Gate U2 (scope lock): autoproduct plan-approve")


@app.command("plan-approve")
def plan_approve(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """Gate U2 — lock scope; changes after this go through an SCR."""
    from autoproduct.upstream import approve_plan, next_tasks

    plan_result = approve_plan(repo_dir)
    ready = next_tasks(repo_dir)
    console.print(f"scope locked: {len(plan_result.tasks)} task(s)")
    for t in ready:
        console.print(f"  ready: {t.id} — autoproduct spec \"{t.description}\"")


@app.command()
def create(
    directory: str = typer.Argument(..., help="Where your product lives (created if new)"),
    profile: str = typer.Option(..., help="web | miniprogram | app"),
    fdr: str = typer.Option(None, help="Your FDR file (default: <dir>/FDR.md)"),
    yes: bool = typer.Option(False, "--yes", help="Confirm the plan and build everything"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
):
    """The non-technical flow: write ONE document (the FDR), the system
    builds the product. First run writes the FDR template + guide."""
    from autoproduct.upstream import init_workspace
    from autoproduct.upstream.autopilot import run_autopilot
    from autoproduct.upstream.fdr import write_template

    root = Path(directory).resolve()
    if not (root / ".mas" / "project.yaml").exists():
        init_workspace(root, root.name, profile)
    fdr_path = Path(fdr) if fdr else root / "FDR.md"
    if not fdr_path.exists() or not fdr_path.read_text(encoding="utf-8").strip():
        write_template(root)
        console.print(
            f"第一步：用自己的话填写 {root / 'FDR.md'}（参考 {root / 'FDR-GUIDE.md'}），"
            f"然后重新运行这条命令。\n"
            f"Step 1: fill in {root / 'FDR.md'} in your own words (see FDR-GUIDE.md), "
            f"then run this command again."
        )
        return

    result = run_autopilot(root, fdr_path, provider=provider, yes=yes)
    if result.status == "needs_answers":
        console.print("[yellow]还需要一些信息 / A few answers needed:[/yellow]")
        for i, q in enumerate(result.assessment.questions, 1):
            console.print(f"  {i}. {q}")
        console.print(f"详见 {root / 'FDR-QUESTIONS.md'} — 补充进 FDR.md 后重新运行。")
        raise typer.Exit(code=2)
    if result.status == "awaiting_confirmation":
        console.print(result.confirmation)
        console.print(f"\n(saved to {root / 'product' / 'CONFIRMATION.md'})")
        raise typer.Exit(code=0)
    color = "green" if result.status == "completed" else "red"
    console.print(f"\n[bold {color}]{result.status}[/bold {color}]")
    for o in result.outcomes:
        verdict = f" · review: {o.review_verdict}" if o.review_verdict else ""
        console.print(f"  {o.task_id} {o.title}: {o.status}{verdict}")
    console.print(f"报告 / report: {result.report_path}")
    if result.status != "completed":
        raise typer.Exit(code=1)


@app.command()
def studio(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    port: int = typer.Option(8433, help="Port"),
    profile: str = typer.Option(None, help="Profile (only needed for a new workspace)"),
):
    """Founder Studio: the browser UI for the FDR flow (localhost only)."""
    from autoproduct.studio import serve_studio
    from autoproduct.upstream import init_workspace

    root = Path(repo_dir).resolve()
    if not (root / ".mas" / "project.yaml").exists():
        if not profile:
            console.print("[red]new workspace: pass --profile web|miniprogram|app[/red]")
            raise typer.Exit(code=2)
        init_workspace(root, root.name, profile)
    console.print(f"Studio: http://127.0.0.1:{port}  (workspace: {root})")
    serve_studio(root, port=port)


@app.command()
def preview(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    port: int = typer.Option(8500, help="Port for the app"),
):
    """Run the built product locally so the founder can try it (web profile)."""
    import subprocess

    from autoproduct.upstream import load_project

    root = Path(repo_dir).resolve()
    project = load_project(root)
    if project.profile == "miniprogram":
        console.print(
            "小程序预览：用微信开发者工具打开这个目录即可（工具 → 导入项目）：\n"
            f"  {root}\n"
            "Open this directory in WeChat DevTools (import project) to preview."
        )
        return
    from autoproduct.upstream.provisioning import preview_env

    for entry in ("app/main.py", "main.py", "app.py"):
        candidate = root / entry
        if candidate.exists():
            console.print(f"starting {entry} — http://127.0.0.1:{port}  (Ctrl-C stops)")
            subprocess.run(
                [sys.executable, str(candidate)],
                cwd=root,
                env={**__import__("os").environ, "PORT": str(port), **preview_env(root)},
            )
            return
    console.print("[yellow]no runnable entry found (looked for app/main.py, main.py, app.py)[/yellow]")
    raise typer.Exit(code=1)


@app.command()
def add(
    fdr: str = typer.Argument(..., help="Feature FDR file (one feature per FDR)"),
    repo_dir: str = typer.Option(".", help="Existing workspace"),
    yes: bool = typer.Option(False, "--yes", help="Confirm and build the feature"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
):
    """Add ONE feature to an existing product from a granular FDR. Keep each
    FDR small: one feature or change per document."""
    from autoproduct.upstream.autopilot import run_feature

    result = run_feature(repo_dir, fdr, provider=provider, yes=yes)
    if result.status == "needs_answers":
        for i, q in enumerate(result.assessment.questions, 1):
            console.print(f"  {i}. {q}")
        raise typer.Exit(code=2)
    if result.status == "awaiting_confirmation":
        console.print(result.confirmation)
        console.print("re-run with --yes to build this feature")
        raise typer.Exit(code=0)
    color = "green" if result.status == "completed" else "red"
    console.print(f"\n[bold {color}]{result.status}[/bold {color}]")
    for o in result.outcomes:
        verdict = f" · review: {o.review_verdict}" if o.review_verdict else ""
        console.print(f"  {o.task_id} {o.title}: {o.status}{verdict}")
    if result.report_path:
        console.print(f"report: {result.report_path}")
    if result.status != "completed":
        raise typer.Exit(code=1)


@app.command()
def scr(
    slug: str = typer.Argument(..., help="Built spec slug that needs changing"),
    reason: str = typer.Argument(..., help="Why the spec must change"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
):
    """Raise a Spec Change Request — the only legal way to change a built
    spec (ADR-U02). A human approves it with scr-approve."""
    from autoproduct.upstream.spec import raise_scr

    path = raise_scr(repo_dir, slug, reason)
    console.print(f"raised: {path.name}\napprove with: autoproduct scr-approve {path.stem.split('-')[1]}")


@app.command("scr-approve")
def scr_approve(
    number: int = typer.Argument(..., help="SCR number"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
):
    """Approve an SCR — grants exactly one regeneration of the named spec."""
    from autoproduct.upstream.spec import approve_scr

    data = approve_scr(repo_dir, number)
    console.print(
        f"approved SCR-{number:03d} for spec {data['spec_slug']!r}: {data['reason']}\n"
        f"the next `autoproduct spec`/`add` touching it may now regenerate it once"
    )


@app.command()
def ship(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """Generate deployment artifacts + a plain-language DEPLOY.md. The
    deploy button stays yours — the system never pushes to production."""
    from autoproduct.upstream.ship import ship as run_ship

    guide = run_ship(repo_dir)
    console.print(f"部署指南已生成 / deploy guide written: {guide}")
    console.print((guide.parent / "Dockerfile").exists() and "Dockerfile ready" or "")


@app.command("product-bench")
def product_bench(
    cases_dir: str = typer.Option(
        str(Path(__file__).resolve().parent.parent.parent / "benchmarks" / "products"),
        help="Labeled product cases (FDR + independent behavioral probes)",
    ),
    provider: str = typer.Option(None, help="Provider (e.g. 'mock')"),
    limit: int = typer.Option(None, help="Run only the first N cases"),
    repo_dir: str = typer.Option(".", help="Where to record the result"),
):
    """Built-product quality, end to end: full autopilot per case, then
    INDEPENDENT probes against the built product (WebGen-Bench pattern)."""
    from autoproduct.product_bench import run_product_bench, save_summary

    summary = run_product_bench(cases_dir, provider=provider, limit=limit)
    table = Table(title="product bench")
    for col in ("case", "autopilot", "built", "probes passed", "clean reviews", "s"):
        table.add_column(col)
    for c in summary.cases:
        table.add_row(
            c.name,
            c.autopilot_status,
            f"{c.tasks_built}/{c.tasks_total}",
            f"{sum(1 for p in c.probes if p.passed)}/{len(c.probes)}",
            f"{c.clean_reviews}/{c.tasks_built}",
            str(c.duration_s),
        )
    console.print(table)
    console.print(
        f"build rate [bold]{summary.build_rate:.0%}[/bold] · "
        f"probe pass [bold]{summary.probe_pass_rate:.0%}[/bold] · "
        f"clean reviews [bold]{summary.clean_review_rate:.0%}[/bold]"
    )
    console.print(f"saved: {save_summary(summary, repo_dir)}")


@app.command()
def recover(repo_dir: str = typer.Option(".", help="Repository the reviews ran in")):
    """Continue reviews that crashed mid-run from their checkpoints."""
    from autoproduct.orchestrator import recover_reviews

    results = recover_reviews(repo_dir)
    if not results:
        console.print("nothing to recover")
        return
    for r in results:
        console.print(f"  {r['review_id']}: {r['status']}"
                      + (f" → {r.get('verdict')}" if r.get("verdict") else ""))


@app.command()
def correct(
    complaint: str = typer.Argument(..., help="What's wrong, in your own words"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
):
    """M3 — 这不是我要的: repairs go through the fix path, scope changes
    raise an SCR (your complaint IS the approval, recorded verbatim)."""
    from autoproduct.upstream.correction import run_correction

    result = run_correction(repo_dir, complaint, provider=provider)
    color = {"fixed": "green", "scr_raised": "yellow"}.get(result.status, "red")
    console.print(f"[bold {color}]{result.status}[/bold {color}] — {result.detail}")
    if result.status == "error":
        raise typer.Exit(code=1)


@app.command()
def walkthrough(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider"),
):
    """M4 — regenerate the 验收清单 (product/ACCEPTANCE.md)."""
    from autoproduct.upstream.walkthrough import generate_walkthrough

    console.print(f"written: {generate_walkthrough(repo_dir, provider=provider)}")


@app.command()
def digest(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider"),
    days: int = typer.Option(7, help="Window"),
):
    """M5 — weekly plain-language digest from the product's own telemetry;
    reconciles the hypothesis ledger with observed events."""
    from autoproduct.upstream.telemetry import generate_digest

    console.print(f"written: {generate_digest(repo_dir, provider=provider, days=days)}")


@app.command("retry-task")
def retry_task(
    task_id: str = typer.Argument(..., help="Failed task id from the report"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider"),
):
    """M7 — retry ONE failed module without rebuilding anything else."""
    from autoproduct.upstream import approve_spec, run_build, run_spec_stage
    from autoproduct.upstream.plan import load_plan

    plan_result = load_plan(repo_dir)
    task = next((t for t in plan_result.tasks if t.id == task_id), None)
    if task is None:
        console.print(f"[red]no task {task_id!r} in the plan[/red]")
        raise typer.Exit(code=1)
    spec = run_spec_stage(repo_dir, f"{task.description} (task:{task.id})", provider=provider)
    if spec.status != "proposed":
        console.print(f"[red]spec blocked: {spec.lint_issues}[/red]")
        raise typer.Exit(code=1)
    approve_spec(repo_dir, spec.slug)
    result = run_build(repo_dir, spec.slug, provider=provider,
                       task_lane=task.lane, task_estimate_hours=task.estimate_hours)
    color = "green" if result.status == "built" else "red"
    console.print(f"[bold {color}]{result.status}[/bold {color}] {result.detail}")
    if result.status != "built":
        raise typer.Exit(code=1)


@app.command()
def undo(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """M7 — 回到上一个版本 (a rescue branch keeps even undo undoable)."""
    from autoproduct.upstream.autopilot import undo_last

    result = undo_last(Path(repo_dir).resolve())
    console.print(f"{result['status']}: {result.get('detail') or result.get('restored_to', '')}")
    if result["status"] == "error":
        raise typer.Exit(code=1)


def main() -> None:
    sys.exit(app())


if __name__ == "__main__":  # `python -m autoproduct.cli` — the server's
    main()                  # detached workers run exactly this (PR #21 bug)
