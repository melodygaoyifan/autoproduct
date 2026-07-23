"""Mutation testing (deep mode) — uses the real mutmut from the dev env on
a tiny fixture, so runs stay in seconds. No network involved."""

import shutil
import subprocess
from pathlib import Path

import pytest

from autoproduct import testing
from autoproduct.testing import run_mutation, run_test_gate

pytestmark = pytest.mark.skipif(
    shutil.which("mutmut") is None, reason="mutmut not on PATH"
)

WELL_TESTED = """\
from calc import add

def test_add():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
"""

CALC_ONLY_ADD = "def add(a, b):\n    return a + b\n"
CALC_WITH_UNTESTED = (
    "def add(a, b):\n    return a + b\n\n"
    "def clamp(x, lo, hi):\n"
    "    if x < lo:\n        return lo\n"
    "    if x > hi:\n        return hi\n"
    "    return x\n"
)


def _repo(tmp_path: Path, source: str) -> Path:
    repo = tmp_path / "proj"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(source)
    (repo / "tests" / "test_calc.py").write_text(WELL_TESTED)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)
    return repo


def test_mutation_passes_on_well_tested_change(tmp_path):
    repo = _repo(tmp_path, CALC_ONLY_ADD)
    report = run_mutation(repo, ["calc.py"])
    assert report.status == "passed"
    assert report.survived == 0
    assert report.score == 1.0


def test_mutation_fails_on_untested_change(tmp_path):
    repo = _repo(tmp_path, CALC_WITH_UNTESTED)
    report = run_mutation(repo, ["calc.py"])
    assert report.status == "failed"
    assert report.survived >= 1
    assert any("clamp" in s for s in report.survivors)


def test_mutation_skips_test_only_changes(tmp_path):
    repo = _repo(tmp_path, CALC_ONLY_ADD)
    report = run_mutation(repo, ["tests/test_calc.py"])
    assert report.status == "skipped"


def test_deep_gate_blocks_on_surviving_mutants(tmp_path, monkeypatch):
    monkeypatch.setattr(testing, "docker_available", lambda: False)
    repo = _repo(tmp_path, CALC_WITH_UNTESTED)
    report = run_test_gate(str(repo), "", mode="deep", changed_files=["calc.py"])
    assert report.status == "passed"  # the suite itself is green
    assert report.sandbox == "subprocess"
    assert report.mutation is not None
    assert report.mutation.status == "failed"
    assert report.gate_blocks  # surviving mutants block APPROVE (§09.5.4.10)


def test_standard_mode_skips_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(testing, "docker_available", lambda: False)
    repo = _repo(tmp_path, CALC_ONLY_ADD)
    report = run_test_gate(str(repo), "", mode="standard", changed_files=["calc.py"])
    assert report.mutation is None
