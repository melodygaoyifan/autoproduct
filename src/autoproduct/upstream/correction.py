"""M3 — the correction loop: "这不是我要的", said after USING the product.

The founder describes what's wrong in plain words. The system maps the
complaint to the responsible built spec, decides whether it's a repair
(the build violates the spec/intent) or a scope change (the spec itself
must change), and drives the existing machinery: repairs go through the
bounded fix path; scope changes go through the SCR channel — the
founder's complaint IS the human authorization, and it is recorded on the
SCR verbatim.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from pydantic import BaseModel

from autoproduct.providers import get_provider
from autoproduct.testing import _pytest_in_subprocess, combine_reports, run_js_tests
from autoproduct.upstream.build import _write_files
from autoproduct.upstream.spec import approve_scr, load_spec, raise_scr
from autoproduct.yamlx import extract_mapping

CORRECTION_MARKER = "correction router for a founder's plain-language complaint"

MAX_REPAIR_ITERATIONS = 3

_ROUTER_SYSTEM = f"""You are the {CORRECTION_MARKER}. Map the complaint to
the ONE responsible feature below and classify it:

- kind: fix — the built product violates the feature's stated criteria or
  obvious intent (wrong text, broken behavior, missing wiring)
- kind: scope_change — the founder wants something the criteria never
  promised (the spec itself must change)

Respond with ONLY YAML:
spec_slug: ...
kind: fix|scope_change
instruction: one concrete sentence for the implementer, in English
"""


class CorrectionResult(BaseModel):
    status: str  # fixed | scr_raised | error
    spec_slug: str = ""
    kind: str = ""
    detail: str = ""
    commit: str | None = None


def _built_specs(root: Path) -> list[dict]:
    specs = []
    for spec_file in sorted(root.glob("specs/*/spec.yaml")):
        data = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        if data.get("built"):
            specs.append(
                {"slug": data["slug"], "title": data.get("title"),
                 "criteria": data.get("criteria", [])}
            )
    return specs


def run_correction(
    repo_dir: str | Path,
    complaint: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> CorrectionResult:
    root = Path(repo_dir).resolve()
    specs = _built_specs(root)
    if not specs:
        return CorrectionResult(status="error", detail="nothing built yet")

    raw = get_provider(provider).complete(
        model=model,
        system=_ROUTER_SYSTEM,
        user=yaml.safe_dump(specs, sort_keys=False, allow_unicode=True)
        + f"\n<complaint>\n{complaint}\n</complaint>",
        max_tokens=512,
    )
    try:
        route = extract_mapping(raw, ("spec_slug",))
    except ValueError as exc:
        return CorrectionResult(status="error", detail=str(exc))
    slug = str(route.get("spec_slug", ""))
    kind = str(route.get("kind", "fix"))
    instruction = str(route.get("instruction", complaint))
    if slug not in {s["slug"] for s in specs}:
        return CorrectionResult(status="error", detail=f"router chose unknown spec {slug!r}")

    if kind == "scope_change":
        # The complaint is the human decision — recorded verbatim on the SCR.
        scr_path = raise_scr(root, slug, f"founder correction: {complaint}")
        number = int(scr_path.stem.split("-")[1])
        approve_scr(root, number)
        return CorrectionResult(
            status="scr_raised", spec_slug=slug, kind=kind,
            detail=f"SCR-{number:03d} approved by founder correction; "
            f"re-run `autoproduct add`/`spec` for {slug!r} to regenerate",
        )

    # Repair path: complaint + spec + implicated sources → smallest change.
    # Bounded ITERATION against the suite (same discipline as build, found
    # necessary by the linkly shakedown): failure output feeds the next
    # attempt; the workspace reverts only after the attempts are exhausted.
    spec = load_spec(root, slug)
    from autoproduct.upstream.build import _related_sources  # reuse mention parser

    related = _related_sources(root, spec)
    base_user = (
        f"<complaint>\n{complaint}\n</complaint>\n\n"
        f"<instruction>\n{instruction}\n</instruction>\n\n"
        f"<spec>\n{yaml.safe_dump(spec.model_dump(include={'title', 'design', 'criteria'}), sort_keys=False, allow_unicode=True)}</spec>\n\n"
        + related
    )
    allowed_tests = {s.path for s in spec.test_skeletons}
    feedback = ""
    written: list[str] = []
    for iteration in range(1, MAX_REPAIR_ITERATIONS + 1):
        current = ""
        if written:
            current = "\n\n".join(
                f'<current_file path="{rel}">\n'
                + (root / rel).read_text(encoding="utf-8", errors="replace")
                + "\n</current_file>"
                for rel in written
                if (root / rel).is_file()
            )
        raw_fix = get_provider(provider).complete(
            model=model,
            system="You are the single-writer implementer in a greenfield product "
            "system, repairing ONE founder complaint. Smallest change; complete "
            "file contents back; never touch tests you did not author.\n\n"
            "Respond with ONLY YAML:\nfiles:\n  - path: ...\n    new_content: |\n      ...",
            user=base_user
            + (f"\n\n{current}" if current else "")
            + (f"\n\n<test_failure attempt=\"{iteration - 1}\">\n{feedback}\n</test_failure>" if feedback else ""),
            max_tokens=16384,
        )
        try:
            data = extract_mapping(raw_fix, ("files",))
            batch = _write_files(root, data.get("files") or [], allowed_test_paths=allowed_tests)
        except ValueError as exc:
            feedback = f"your previous response failed: {exc}"
            continue
        if not batch:
            feedback = "you returned no files; return the corrected file contents"
            continue
        written = sorted(set(written) | set(batch))
        report = combine_reports(_pytest_in_subprocess(root), run_js_tests(root))
        if report.status not in ("failed", "error"):
            subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
            committed = subprocess.run(
                ["git", "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
                 "commit", "-qm", f"fix({slug}): founder correction — {complaint[:60]}"],
                cwd=root, capture_output=True, text=True,
            )
            if committed.returncode != 0:
                return CorrectionResult(status="error", spec_slug=slug, kind=kind,
                                        detail="no effective change produced")
            sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                                 capture_output=True, text=True).stdout.strip()
            return CorrectionResult(
                status="fixed", spec_slug=slug, kind=kind, commit=sha,
                detail=f"repaired in {iteration} attempt(s); files: {', '.join(written)}",
            )
        feedback = report.detail or report.summary
    subprocess.run(["git", "checkout", "--", "."], cwd=root, capture_output=True)
    subprocess.run(["git", "clean", "-fdq", "--exclude=.mas", "--exclude=data"],
                   cwd=root, capture_output=True)
    return CorrectionResult(
        status="error", spec_slug=slug, kind=kind,
        detail=f"repair still broke the suite after {MAX_REPAIR_ITERATIONS} "
        f"attempt(s) ({feedback[:120]}); workspace reverted",
    )
