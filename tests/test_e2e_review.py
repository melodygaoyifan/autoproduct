"""End-to-end graph run against the mock provider — hermetic (no network,
no API keys). The fixture diff plants three bugs; the pipeline must catch
them and emit the full artifact trail."""

from pathlib import Path

import yaml

from autoproduct.orchestrator import run_review
from autoproduct.state import Verdict


def test_planted_bugs_found_end_to_end(tmp_path, planted_diff_text, skills_dir):
    result, state = run_review(
        "fixture://planted",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=planted_diff_text,
    )

    assert state["dor_pass"]
    assert state["mode"] == "standard"
    assert result is not None
    assert result.verdict is Verdict.REQUEST_CHANGES
    titles = {f.title for f in result.findings}
    assert "Swallowed exception" in titles
    assert "eval() on untrusted input" in titles

    mirror_files = sorted(Path(state["artifacts_dir"]).glob("*.yaml"))
    assert [p.name.split("-", 1)[1] for p in mirror_files] == [
        "dor_gate.yaml",
        "init.yaml",
        "analyze.yaml",
        "tools.yaml",
        "vote.yaml",
        "verify.yaml",
        "leader.yaml",
        "final.yaml",
    ]
    final = yaml.safe_load(mirror_files[-1].read_text())
    assert final["verdict"] == "REQUEST_CHANGES"


def test_empty_diff_fails_dor_gate(tmp_path, skills_dir):
    result, state = run_review(
        "fixture://empty",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text="",
    )
    assert not state["dor_pass"]
    assert result is None
    assert any("empty diff" in r for r in state["dor_reasons"])


def test_oversized_diff_fails_dor_gate(tmp_path, skills_dir):
    lines = "\n".join(f"+line {i}" for i in range(2100))
    big = (
        "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n"
        f"@@ -1,1 +1,2101 @@\n{lines}\n"
    )
    _, state = run_review(
        "fixture://big",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=big,
    )
    assert not state["dor_pass"]
