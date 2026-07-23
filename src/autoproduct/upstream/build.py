"""Coding stage (§13, ADR-U01) — single-writer implementer, test-first.

Deliberately NOT a voting stage: generation is single-writer; judgment
lives in the review stage the diff is handed to afterwards. The build
gate is deterministic — the spec's test skeletons (and everything else in
the suite) must pass before the commit exists.

Bounds: ≤12 files, ≤500 lines each, repo-relative paths only, never
.git/.mas/specs. ≤3 implement-run-fix iterations, then BUILD_FAILED.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autoproduct.providers import get_provider
from autoproduct.testing import _pytest_in_docker, _pytest_in_subprocess, docker_available
from autoproduct.upstream.spec import Spec, load_spec
from autoproduct.upstream.workspace import load_project
from autoproduct.yamlx import extract_mapping

IMPLEMENTER_MARKER = "single-writer implementer in a greenfield product system"

MAX_ITERATIONS = 3
_MAX_FILES = 12
_MAX_FILE_LINES = 500
_FORBIDDEN_PREFIXES = (".git", ".mas", "specs")


class BuildResult(BaseModel):
    slug: str
    status: str  # built | build_failed | error
    iterations: int = 0
    files_written: list[str] = Field(default_factory=list)
    modified_existing: list[str] = Field(
        default_factory=list, description="scope_check-lite: pre-existing files "
        "the implementer changed — visible, reviewed, never silent"
    )
    wireup_issues: list[str] = Field(default_factory=list)
    test_summary: str = ""
    commit: str | None = None
    detail: str = ""


_SYSTEM = f"""You are the {IMPLEMENTER_MARKER}. Implement the approved spec
below, test-first: the test skeletons are the contract — write them as real
tests that encode the EARS criteria, then the smallest implementation that
passes them.

Rules:
- Return COMPLETE file contents (no diffs). At most {_MAX_FILES} files,
  each under {_MAX_FILE_LINES} lines. Include the test files.
- Respect the project constraints; no new dependencies unless the spec's
  design names them.
- Never touch paths under {_FORBIDDEN_PREFIXES}.

Respond with ONLY YAML:
files:
  - path: ...
    new_content: |
      ...
notes: one line
"""


def _run_tests(repo: Path):
    return (
        _pytest_in_docker(repo) if docker_available() else _pytest_in_subprocess(repo)
    )


def _write_files(
    repo: Path, files: list[dict], *, allowed_test_paths: set[str] | None = None
) -> list[str]:
    written = []
    for f in files[:_MAX_FILES]:
        rel = str(f["path"]).lstrip("/")
        if any(rel.startswith(p) for p in _FORBIDDEN_PREFIXES) or ".." in rel:
            raise ValueError(f"implementer touched forbidden path {rel!r}")
        # §13.29.5 write-lock: existing non-skeleton tests are read-only to
        # the implementer. A blocking test is either its bug or a spec gap —
        # never a test to edit. (Reward-hacking defense, structural.)
        is_test = rel.startswith("tests/") or Path(rel).name.startswith("test_")
        if (
            is_test
            and (repo / rel).exists()
            and allowed_test_paths is not None
            and rel not in allowed_test_paths
        ):
            raise ValueError(
                f"implementer tried to modify existing test {rel!r} — existing "
                "tests are read-only (fix the code, or the spec is wrong)"
            )
        content = str(f["new_content"])
        if len(content.splitlines()) > _MAX_FILE_LINES:
            raise ValueError(f"{rel} exceeds {_MAX_FILE_LINES} lines")
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    return written


def _file_tree(repo: Path, cap: int = 200) -> str:
    lines = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo)
        if any(part in (".git", ".mas", "__pycache__", "node_modules", ".venv") for part in rel.parts):
            continue
        lines.append(str(rel))
        if len(lines) >= cap:
            lines.append("… (truncated)")
            break
    return "\n".join(lines)


def _related_sources(repo: Path, spec: Spec, cap_files: int = 6, cap_lines: int = 200) -> str:
    """Existing files the spec's design mentions — the implementer extends
    the product, it does not recreate it (feature-FDR awareness)."""
    mentioned = re.findall(r"[\w/]+\.(?:py|js|ts|wxml|wxss|json|html)", spec.design)
    blocks = []
    for rel in dict.fromkeys(mentioned):
        path = repo / rel
        if path.is_file():
            text = "\n".join(
                path.read_text(encoding="utf-8", errors="replace").splitlines()[:cap_lines]
            )
            blocks.append(f'<existing_file path="{rel}">\n{text}\n</existing_file>')
        if len(blocks) >= cap_files:
            break
    return "\n\n".join(blocks)


def run_build(
    repo_dir: str | Path,
    slug: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> BuildResult:
    repo = Path(repo_dir).resolve()
    project = load_project(repo)
    spec: Spec = load_spec(repo, slug)
    if spec.status != "approved":
        return BuildResult(
            slug=slug,
            status="error",
            detail=f"spec status is {spec.status!r} — Gate U3 requires "
            f"`autoproduct spec-approve {slug}` first",
        )

    provider_impl = get_provider(provider)
    claude_md = repo / "CLAUDE.md"
    constraints = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    existing = _related_sources(repo, spec)
    base_user = (
        f"<constraints>\n{constraints}\n</constraints>\n\n"
        f"<repo_tree>\n{_file_tree(repo)}\n</repo_tree>\n\n"
        + (f"{existing}\n\n" if existing else "")
        + "You are EXTENDING the existing product above — integrate with it, "
        "never recreate it. Existing test files are read-only to you.\n\n"
        f"<spec>\n{yaml.safe_dump(spec.model_dump(include={'title', 'design', 'criteria'}), sort_keys=False, allow_unicode=True)}"
        f"test_skeletons:\n"
        + "\n".join(f"- {s.path}: {s.purpose} (covers {s.covers})" for s in spec.test_skeletons)
        + "\n</spec>"
    )
    allowed_tests = {s.path for s in spec.test_skeletons}
    pre_existing = {
        str(p.relative_to(repo))
        for p in repo.rglob("*")
        if p.is_file() and ".git" not in p.parts and ".mas" not in p.parts
    }

    feedback = ""
    written: list[str] = []
    report = None
    for iteration in range(1, MAX_ITERATIONS + 1):
        raw = provider_impl.complete(
            model=model,
            system=_SYSTEM,
            user=base_user
            + (f"\n\n<test_failure>\n{feedback}\n</test_failure>" if feedback else ""),
            max_tokens=16384,
        )
        try:
            data = extract_mapping(raw, ("files",))
            written = _write_files(
                repo, data.get("files") or [], allowed_test_paths=allowed_tests
            )
        except ValueError as exc:
            return BuildResult(slug=slug, status="error", iterations=iteration, detail=str(exc))
        if not written:
            return BuildResult(
                slug=slug, status="error", iterations=iteration, detail="implementer returned no files"
            )
        report = _run_tests(repo)
        python_skeletons = any(s.path.endswith(".py") for s in spec.test_skeletons)
        if report.status == "passed" or (
            report.status == "no_tests" and not python_skeletons
        ):
            # Non-Python stacks (小程序 WXML/JS, RN) have no pytest gate yet;
            # their skeletons run under the profile's own runner (future
            # work) and the review stage still judges the diff.
            break
        feedback = report.detail or report.summary
    else:
        return BuildResult(
            slug=slug,
            status="build_failed",
            iterations=MAX_ITERATIONS,
            files_written=written,
            test_summary=report.summary if report else "",
            detail="suite still failing after max iterations; nothing committed "
            "(worktree left for inspection)",
        )

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    committed = subprocess.run(
        ["git", "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
         "commit", "-qm",
         f"feat({slug}): {spec.title}\n\nImplements spec {slug} (Gate U4 build "
         f"gate passed: {report.summary}). Review with: autoproduct review HEAD~1"],
        cwd=repo, capture_output=True, text=True,
    )
    if committed.returncode != 0:
        return BuildResult(
            slug=slug, status="error", iterations=iteration,
            files_written=written, detail=committed.stderr[:300],
        )
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    from autoproduct.tools.wireup import wireup_check

    wireup = wireup_check(repo)
    return BuildResult(
        slug=slug,
        status="built",
        iterations=iteration,
        files_written=written,
        modified_existing=sorted(set(written) & pre_existing),
        wireup_issues=[f.title for f in wireup.findings][:10],
        test_summary=report.summary,
        commit=sha,
    )
