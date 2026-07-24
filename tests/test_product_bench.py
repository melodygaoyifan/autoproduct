import shutil
from pathlib import Path

import pytest

from autoproduct import testing as testing_mod
from autoproduct.product_bench import load_cases, run_case

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

CASES = Path(__file__).parent.parent / "benchmarks" / "products"


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import autoproduct.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def test_cases_load():
    cases = load_cases(CASES)
    assert len(cases) == 5
    assert all(c.probes for c in cases)


def test_feature_add_case_accretes_modules(tmp_path):
    case = next(c for c in load_cases(CASES) if c.name == "04-feature-add")
    result = run_case(case, provider="mock", keep_dir=tmp_path / "keep")
    assert result.autopilot_status == "completed"
    assert result.tasks_total == 6  # 3 base + 3 feature tasks
    assert result.probe_pass_rate == 1.0, [p.model_dump() for p in result.probes]


def test_miniprogram_profile_case(tmp_path):
    case = next(c for c in load_cases(CASES) if c.name == "05-miniprogram-profile")
    result = run_case(case, provider="mock", keep_dir=tmp_path / "keep")
    assert result.autopilot_status == "completed"
    assert result.probe_pass_rate == 1.0


def test_probes_pass_against_mock_built_product(tmp_path):
    case = load_cases(CASES)[0]  # 01-item-store
    result = run_case(case, provider="mock", keep_dir=tmp_path / "keep")
    assert result.autopilot_status == "completed"
    assert result.build_rate == 1.0
    # Independent probes exercise the BUILT modules, not the builder's tests.
    assert result.probe_pass_rate == 1.0, [p.model_dump() for p in result.probes]
    assert result.clean_review_rate == 1.0


def test_bench_is_honest_about_failing_probes(tmp_path):
    case = next(c for c in load_cases(CASES) if c.name == "03-honesty-check")
    result = run_case(case, provider="mock", keep_dir=tmp_path / "keep")
    assert result.build_rate == 1.0  # everything built…
    assert result.probe_pass_rate < 1.0  # …but the impossible probe fails, visibly
    failing = [p for p in result.probes if not p.passed]
    assert failing and "unreasonable-demand" in failing[0].name
