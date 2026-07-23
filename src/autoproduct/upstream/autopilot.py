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
    confirmation = provider_impl.complete(
        model=model,
        system=_CONFIRM_SYSTEM,
        user=yaml.safe_dump(
            brief.model_dump(include={"title", "scope_now", "scope_later", "scope_never", "success_metrics"}),
            sort_keys=False, allow_unicode=True,
        )
        + f"\n<fdr_language_sample>\n{fdr_text[:400]}\n</fdr_language_sample>",
        max_tokens=1024,
    )
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
        built = run_build(root, spec.slug, provider=provider, model=model)
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
    return AutopilotResult(
        status="completed" if built_count == len(outcomes) and outcomes else "failed",
        assessment=assessment,
        confirmation=confirmation,
        outcomes=outcomes,
        report_path=str(report_path),
        auto_approvals=auto_approvals,
    )


def _review_head(root: Path, provider: str):
    from autoproduct.orchestrator import run_review

    skills = Path(__file__).resolve().parent.parent.parent.parent / "skills"
    review, _ = run_review(
        "HEAD~1", repo_dir=str(root), skills_dir=str(skills),
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
