import subprocess
from pathlib import Path

import yaml

from autoproduct.orchestrator import run_review
from autoproduct.testing import run_test_gate


def _git_repo(tmp_path: Path, test_body: str) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(test_body)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)
    return repo


PASSING = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
FAILING = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 4\n"


def test_gate_passes_on_green_suite(tmp_path):
    repo = _git_repo(tmp_path, PASSING)
    report = run_test_gate(str(repo), "")
    assert report.status == "passed"
    assert not report.gate_blocks


def test_gate_fails_on_red_suite(tmp_path):
    repo = _git_repo(tmp_path, FAILING)
    report = run_test_gate(str(repo), "")
    assert report.status == "failed"
    assert report.gate_blocks


def test_gate_applies_diff_in_worktree_not_checkout(tmp_path):
    repo = _git_repo(tmp_path, PASSING)
    # Diff breaks add(); suite must fail in the worktree while the user's
    # checkout stays untouched.
    breaking_diff = """\
diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""
    report = run_test_gate(str(repo), breaking_diff)
    assert report.status == "failed"
    assert "return a + b" in (repo / "calc.py").read_text()


def test_gate_skipped_outside_git(tmp_path):
    assert run_test_gate(str(tmp_path), "").status == "skipped"


def test_gate_error_blocks_approve(tmp_path):
    """Found by self-review of PR #3: an unrunnable suite must not let
    APPROVE survive (charter rule 9)."""
    repo = _git_repo(tmp_path, PASSING)
    non_applying = """\
diff --git a/nonexistent.py b/nonexistent.py
--- a/nonexistent.py
+++ b/nonexistent.py
@@ -1,1 +1,1 @@
-old line that is not there
+new line
"""
    report = run_test_gate(str(repo), non_applying)
    assert report.status == "error"
    assert report.gate_blocks


def test_e2e_gate2_downgrades_approve(tmp_path, skills_dir):
    """Mock voters find nothing in a benign diff -> APPROVE, but the failing
    suite must force REQUEST_CHANGES."""
    repo = _git_repo(tmp_path, FAILING)
    benign_diff = """\
diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,3 @@
 def add(a, b):
     return a + b
+# benign trailing comment
"""
    result, state = run_review(
        "fixture://gate2",
        repo_dir=str(repo),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=benign_diff,
    )
    assert result.verdict.value == "REQUEST_CHANGES"
    assert "Gate 2" in result.summary
    assert state["test_report"]["status"] == "failed"


def test_e2e_voter_logs_appended(tmp_path, planted_diff_text, skills_dir):
    _, state = run_review(
        "fixture://logs",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=planted_diff_text,
    )
    log = tmp_path / ".mas" / "voters" / "correctness" / "log.yaml"
    entries = yaml.safe_load(log.read_text())
    assert entries[0]["review_id"] == state["review_id"]
    assert entries[0]["status"] == "OK"
