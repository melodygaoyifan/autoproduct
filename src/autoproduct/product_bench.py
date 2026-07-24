"""Product benchmark — built-product quality, measured end to end.

The review benchmark measures whether the system judges code well; this
measures whether it BUILDS products well. Architecture follows the
WebGen-Bench insight (arXiv:2505.03733): quality is what INDEPENDENT
probes observe when exercised against the built product — never the
builder's own tests (circular) and never review verdicts alone.

A case = an FDR + behavioral probes. The full autopilot runs in a fresh
workspace; each probe is a self-contained script executed IN the built
workspace with the product's runtime env. Scores:

- build_rate: tasks that reached `built`
- probe_pass_rate: independent behaviors that actually work
- clean_review_rate: built tasks whose review was APPROVE-class

The composite is deliberately NOT averaged away: all three numbers are
reported; a build that compiles but fails its probes is visible as
exactly that.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autoproduct.upstream import init_workspace
from autoproduct.upstream.autopilot import run_autopilot
from autoproduct.upstream.provisioning import preview_env

_PROBE_TIMEOUT_S = 60


class Probe(BaseModel):
    name: str
    script: str  # python source, exit 0 = behavior works


class ProductCase(BaseModel):
    name: str
    profile: str = "web"
    fdr: str
    feature_fdrs: list[str] = Field(
        default_factory=list,
        description="granular follow-up FDRs applied via the feature flow",
    )
    probes: list[Probe] = Field(default_factory=list)
    auto_probes: bool = Field(
        default=False,
        description="generate probes from the FDR against the built product "
        "(the real-user path) instead of hand-written fixtures",
    )

    def model_post_init(self, _ctx) -> None:
        if not self.probes and not self.auto_probes:
            raise ValueError("case needs probes or auto_probes: true")


class ProbeResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class CaseResult(BaseModel):
    name: str
    autopilot_status: str
    tasks_total: int = 0
    tasks_built: int = 0
    clean_reviews: int = 0
    outcomes: list[dict] = Field(
        default_factory=list, description="per-task forensics: status + detail"
    )
    preserved_workspace: str = ""
    probes: list[ProbeResult] = Field(default_factory=list)
    duration_s: float = 0.0

    @property
    def build_rate(self) -> float:
        return self.tasks_built / self.tasks_total if self.tasks_total else 0.0

    @property
    def probe_pass_rate(self) -> float:
        return (
            sum(1 for p in self.probes if p.passed) / len(self.probes)
            if self.probes
            else 0.0
        )

    @property
    def clean_review_rate(self) -> float:
        return self.clean_reviews / self.tasks_built if self.tasks_built else 0.0


class BenchSummary(BaseModel):
    cases: list[CaseResult]
    build_rate: float
    probe_pass_rate: float
    clean_review_rate: float


def load_cases(cases_dir: str | Path) -> list[ProductCase]:
    cases = [
        ProductCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(Path(cases_dir).glob("*.yaml"))
    ]
    if not cases:
        raise FileNotFoundError(f"no product cases in {cases_dir}")
    return cases


def workspace_python(workspace: Path) -> str:
    """Environment parity: if the built product declares dependencies,
    probes (and the product they boot) run in an isolated env built from
    the product's OWN requirements — a framework outside autoproduct's
    venv must not read as a product failure."""
    import shutil

    requirements = workspace / "requirements.txt"
    if not requirements.exists() or not shutil.which("uv"):
        return sys.executable
    real_deps = [
        line.strip()
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not real_deps:
        return sys.executable
    venv_python = workspace / ".probe-venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    created = subprocess.run(
        ["uv", "venv", str(workspace / ".probe-venv")],
        capture_output=True, text=True, timeout=120,
    )
    if created.returncode != 0:
        return sys.executable
    installed = subprocess.run(
        ["uv", "pip", "install", "-r", str(requirements),
         "--python", str(venv_python)],
        capture_output=True, text=True, timeout=300,
    )
    return str(venv_python) if installed.returncode == 0 else sys.executable


def run_probe(workspace: Path, probe: Probe) -> ProbeResult:
    """The probe runs IN the built workspace with the product's runtime
    env — it observes the product from outside, like a user's script."""
    import os

    with tempfile.NamedTemporaryFile(
        "w", suffix=f"-{probe.name}.py", delete=False
    ) as handle:
        handle.write(probe.script)
        probe_path = handle.name
    try:
        proc = subprocess.run(
            [workspace_python(workspace), probe_path],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            env={**os.environ, "PYTHONPATH": str(workspace), **preview_env(workspace)},
        )
        detail = (proc.stdout or proc.stderr).strip().splitlines()
        return ProbeResult(
            name=probe.name,
            passed=proc.returncode == 0,
            detail=detail[-1][:200] if detail else "",
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(name=probe.name, passed=False, detail="probe timed out")
    finally:
        Path(probe_path).unlink(missing_ok=True)


def run_case(case: ProductCase, *, provider: str | None = None) -> CaseResult:
    import time

    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="autoproduct-productbench-") as tmp:
        workspace = init_workspace(Path(tmp) / case.name, case.name, case.profile)
        (workspace / "FDR.md").write_text(case.fdr, encoding="utf-8")
        result = run_autopilot(
            workspace,
            workspace / "FDR.md",
            provider=provider or "anthropic",
            yes=True,
        )
        all_outcomes = list(result.outcomes)
        statuses = [result.status]
        if result.status == "completed" and case.feature_fdrs:
            from autoproduct.upstream.autopilot import run_feature

            for i, feature_fdr in enumerate(case.feature_fdrs):
                fdr_path = workspace / f".bench-feature-{i}.md"
                fdr_path.write_text(feature_fdr, encoding="utf-8")
                feature_result = run_feature(
                    workspace, fdr_path, provider=provider or "anthropic", yes=True
                )
                statuses.append(feature_result.status)
                all_outcomes += feature_result.outcomes
            result.outcomes = all_outcomes
            if any(s != "completed" for s in statuses):
                result.status = "failed"

        built = [o for o in result.outcomes if o.status == "built"]
        clean = [
            o for o in built
            if o.review_verdict in ("APPROVE", "APPROVE_WITH_NOTES")
        ]
        case_probes = list(case.probes)
        if case.auto_probes:
            from autoproduct.upstream.probegen import generate_probes

            generated, _ = generate_probes(
                workspace, provider=provider or "anthropic"
            )
            case_probes += [Probe(name=g.name, script=g.script) for g in generated]
        probes = [run_probe(workspace, probe) for probe in case_probes]
        preserved = ""
        if result.status != "completed" or not all(p.passed for p in probes):
            # Failure forensics: the temp workspace would vanish with the
            # scoreboard's most important evidence.
            import shutil as _shutil

            keep = Path(".mas") / "product-bench" / "workspaces" / case.name
            _shutil.rmtree(keep, ignore_errors=True)
            keep.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copytree(workspace, keep, ignore=_shutil.ignore_patterns(".probe-venv"))
            preserved = str(keep)
        return CaseResult(
            name=case.name,
            autopilot_status=result.status,
            tasks_total=len(result.outcomes),
            tasks_built=len(built),
            clean_reviews=len(clean),
            outcomes=[
                {"task_id": o.task_id, "title": o.title, "status": o.status,
                 "review": o.review_verdict, "detail": o.detail[:200]}
                for o in result.outcomes
            ],
            preserved_workspace=preserved,
            probes=probes,
            duration_s=round(time.monotonic() - start, 1),
        )


def run_product_bench(
    cases_dir: str | Path, *, provider: str | None = None, limit: int | None = None
) -> BenchSummary:
    cases = load_cases(cases_dir)[: limit or None]
    results = []
    for case in cases:
        try:
            results.append(run_case(case, provider=provider))
        except Exception as exc:  # noqa: BLE001 — one case never kills the bench
            results.append(
                CaseResult(
                    name=case.name,
                    autopilot_status=f"error: {type(exc).__name__}: {str(exc)[:120]}",
                )
            )

    def _avg(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return BenchSummary(
        cases=results,
        build_rate=_avg([r.build_rate for r in results]),
        probe_pass_rate=_avg([r.probe_pass_rate for r in results]),
        clean_review_rate=_avg([r.clean_review_rate for r in results]),
    )


def save_summary(summary: BenchSummary, repo_dir: str | Path) -> Path:
    out_dir = Path(repo_dir) / ".mas" / "product-bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d-%H%M")
    path = out_dir / f"result-{stamp}.yaml"
    payload = summary.model_dump(mode="json")
    payload["rates"] = {
        "build_rate": round(summary.build_rate, 3),
        "probe_pass_rate": round(summary.probe_pass_rate, 3),
        "clean_review_rate": round(summary.clean_review_rate, 3),
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path
