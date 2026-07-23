from pathlib import Path

from autoproduct.bench import load_cases, run_benchmark

CASES = Path(__file__).parent.parent / "benchmarks" / "cases"
SKILLS = Path(__file__).parent.parent / "skills"


def test_cases_load_and_are_labeled():
    cases = load_cases(CASES)
    assert len(cases) == 10
    defect_cases = [c for c in cases if c.expected]
    clean_cases = [c for c in cases if not c.expected]
    assert len(defect_cases) == 8
    assert len(clean_cases) == 2


def test_mock_benchmark_scores_deterministic_slice():
    """The mock voter plants sql/eval/except patterns and the deterministic
    tools catch secret/typosquat/csrf/ssrf — so this slice is a floor the
    harness must always credit correctly."""
    result = run_benchmark(CASES, skills_dir=str(SKILLS), provider_override="mock")
    by_name = {c.name: c for c in result.cases}

    for name in (
        "01-sql-injection",
        "03-swallowed-exception",
        "04-eval-user-input",
        "05-hardcoded-secret",
        "06-typosquat-dependency",
        "07-csrf-missing",
        "08-ssrf-variable-url",
    ):
        assert by_name[name].expected_matched == 1, name

    # Clean cases must stay clean for the mock+tools stack.
    assert by_name["09-clean-rename"].findings_total == 0
    assert by_name["10-clean-constant"].findings_total == 0

    # 02-missing-where needs real semantic review; mock can't see it.
    assert result.recall >= 7 / 8 - 0.01 or by_name["02-missing-where"].expected_matched == 0
    assert result.precision == 1.0
