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


def main() -> None:
    sys.exit(app())
