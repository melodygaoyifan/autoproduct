"""Benchmark runner (§09.9, completion criterion #3).

Labeled diffs with known defects; the pipeline runs each and we score:

- recall    = expected defects matched / expected defects total
- precision = kept findings matching some expected defect / kept findings

v0.1.0 bars (doc 10 Week 5): recall ≥ 40%, precision ≥ 50%. Negative
(clean) cases contribute only to precision — any finding there is a false
positive. Matching is deliberately coarse (file + keyword) so the harness
never over-credits a lucky guess in the wrong file.
"""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autoproduct.orchestrator import run_review
from autoproduct.state import LeaderResult


class ExpectedDefect(BaseModel):
    file_path: str
    keywords: list[str] = Field(min_length=1, description="any-match, lowercase")


class BenchCase(BaseModel):
    name: str
    description: str = ""
    diff: str
    expected: list[ExpectedDefect] = Field(default_factory=list)


class CaseResult(BaseModel):
    name: str
    verdict: str
    expected_total: int
    expected_matched: int
    findings_total: int
    findings_matched: int
    duration_s: float


class BenchResult(BaseModel):
    cases: list[CaseResult]
    recall: float
    precision: float

    def passes(self, *, recall_min: float = 0.40, precision_min: float = 0.50) -> bool:
        return self.recall >= recall_min and self.precision >= precision_min


def load_cases(cases_dir: str | Path) -> list[BenchCase]:
    cases = [
        BenchCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(Path(cases_dir).glob("*.yaml"))
    ]
    if not cases:
        raise FileNotFoundError(f"no benchmark cases in {cases_dir}")
    return cases


def _matches(finding: dict, expected: ExpectedDefect) -> bool:
    if expected.file_path != finding.get("file_path"):
        return False
    haystack = f"{finding.get('title', '')} {finding.get('explanation', '')}".lower()
    return any(keyword in haystack for keyword in expected.keywords)


def run_case(
    case: BenchCase, *, skills_dir: str, provider_override: str | None
) -> CaseResult:
    import time

    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="autoproduct-bench-") as tmp:
        result, state = run_review(
            f"bench://{case.name}",
            repo_dir=tmp,
            skills_dir=skills_dir,
            provider_override=provider_override,
            diff_text=case.diff,
        )
    duration = time.monotonic() - start
    leader: LeaderResult | None = result
    findings = [f.model_dump(mode="json") for f in leader.findings] if leader else []
    matched_expected = sum(
        1 for exp in case.expected if any(_matches(f, exp) for f in findings)
    )
    matched_findings = sum(
        1 for f in findings if any(_matches(f, exp) for exp in case.expected)
    )
    return CaseResult(
        name=case.name,
        verdict=leader.verdict.value if leader else "(none)",
        expected_total=len(case.expected),
        expected_matched=matched_expected,
        findings_total=len(findings),
        findings_matched=matched_findings,
        duration_s=round(duration, 1),
    )


def run_benchmark(
    cases_dir: str | Path,
    *,
    skills_dir: str,
    provider_override: str | None = None,
    limit: int | None = None,
) -> BenchResult:
    cases = load_cases(cases_dir)[: limit or None]
    results = [
        run_case(c, skills_dir=skills_dir, provider_override=provider_override)
        for c in cases
    ]
    expected_total = sum(r.expected_total for r in results)
    expected_matched = sum(r.expected_matched for r in results)
    findings_total = sum(r.findings_total for r in results)
    findings_matched = sum(r.findings_matched for r in results)
    return BenchResult(
        cases=results,
        recall=expected_matched / expected_total if expected_total else 1.0,
        precision=findings_matched / findings_total if findings_total else 1.0,
    )


def save_result(result: BenchResult, repo_dir: str | Path) -> Path:
    out_dir = Path(repo_dir) / ".mas" / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d-%H%M")
    path = out_dir / f"result-{stamp}.yaml"
    path.write_text(
        yaml.safe_dump(result.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return path
