import shutil
import subprocess
from pathlib import Path

import pytest

from autoproduct.maintenance.fixpr import generate_fix_pr
from autoproduct.maintenance.review import Incident, RootCauseResult

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

BUGGY = "def add(a, b):\n    return a - b\n"
TEST = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def _repo(tmp_path: Path, source=BUGGY) -> Path:
    repo = tmp_path / "proj"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(source)
    (repo / "tests" / "test_calc.py").write_text(TEST)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)
    return repo


INCIDENT = Incident(id="inc9", title="add() returns wrong totals", body="sums are wrong")
ROOT_CAUSE = RootCauseResult(
    hypothesis="add() subtracts instead of adding",
    confidence=80,
    implicated_files=["calc.py"],
)


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    # Hermetic: fix verification uses the subprocess path in tests.
    monkeypatch.setattr(
        "autoproduct.maintenance.fixpr.docker_available", lambda: False
    )


def test_fix_pr_creates_branch_with_passing_fix(tmp_path):
    repo = _repo(tmp_path)
    attempt = generate_fix_pr(INCIDENT, ROOT_CAUSE, repo_dir=str(repo), provider="mock")
    # No remote in the fixture repo: push fails -> branch_only, never 'opened'.
    assert attempt.status == "branch_only"
    assert attempt.branch == "autoproduct/fix-inc9"
    assert attempt.files_changed == ["calc.py"]
    show = subprocess.run(
        ["git", "show", f"{attempt.branch}:calc.py"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    assert "return a + b" in show.stdout
    # The user's checkout is untouched — the fix lived in a worktree.
    assert "return a - b" in (repo / "calc.py").read_text()


def test_fix_abandoned_when_tests_still_fail(tmp_path):
    # Plant a bug the mock fixer doesn't know: fix won't apply -> abstain,
    # OR apply-but-fail path via a fixer that "fixes" the wrong thing.
    repo = _repo(tmp_path, source="def add(a, b):\n    return a * b\n")
    attempt = generate_fix_pr(INCIDENT, ROOT_CAUSE, repo_dir=str(repo), provider="mock")
    assert attempt.status == "abstained"
    branches = subprocess.run(
        ["git", "branch", "--list", "autoproduct/*"],
        cwd=repo, capture_output=True, text=True,
    ).stdout
    assert branches.strip() == ""  # nothing pushed, nothing left behind


def test_fix_abstains_on_missing_files(tmp_path):
    repo = _repo(tmp_path)
    ghost = RootCauseResult(
        hypothesis="x", confidence=80, implicated_files=["nonexistent.py"]
    )
    attempt = generate_fix_pr(INCIDENT, ghost, repo_dir=str(repo), provider="mock")
    assert attempt.status == "abstained"
