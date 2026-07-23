import shutil

import pytest

from autoproduct import testing as testing_mod
from autoproduct.upstream import init_workspace
from autoproduct.upstream.autopilot import run_autopilot
from autoproduct.upstream.fdr import write_template

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

GOOD_FDR = """# 产品需求
小区团长发起团购接龙，邻居在小程序里下单，团长看到按商品汇总的数量和应收金额。
必须有：发起接龙、下单、汇总。暂时不要：在线支付。
成功：第一周 10 个团长发起过接龙。
"""


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import autoproduct.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def _workspace(tmp_path, fdr_text: str):
    root = init_workspace(tmp_path / "prod", "prod", "miniprogram")
    (root / "FDR.md").write_text(fdr_text, encoding="utf-8")
    return root


def test_template_and_guide_written(tmp_path):
    path = write_template(tmp_path / "w")
    assert path.name == "FDR.md"
    assert "不需要任何技术词汇" in path.read_text()
    guide = (tmp_path / "w" / "FDR-GUIDE.md").read_text()
    assert "Four rules" in guide
    assert "One FDR = one thing" in guide  # the granularity contract


def test_inadequate_fdr_yields_questions_not_a_build(tmp_path):
    root = _workspace(tmp_path, "just an idea: something for my neighborhood")
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)
    assert result.status == "needs_answers"
    questions = (root / "FDR-QUESTIONS.md").read_text()
    assert "谁会用它" in questions
    assert not (root / "product" / "plan.yaml").exists()  # nothing built on guesses


def test_confirmation_pause_without_yes(tmp_path):
    root = _workspace(tmp_path, GOOD_FDR)
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=False)
    assert result.status == "awaiting_confirmation"
    assert (root / "product" / "CONFIRMATION.md").exists()
    assert not (root / "product" / "plan.yaml").exists()  # paused before building


def test_full_autopilot_builds_every_task(tmp_path):
    root = _workspace(tmp_path, GOOD_FDR)
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)
    assert result.status == "completed", [o.model_dump() for o in result.outcomes]
    assert len(result.outcomes) == 3  # mock plan: t1 -> t2 -> t3
    assert all(o.status == "built" for o in result.outcomes)
    assert all(o.review_verdict for o in result.outcomes)
    # Every machine approval is on the record, never silent.
    assert any("Gate U2" in a for a in result.auto_approvals)
    assert sum("Gate U3" in a for a in result.auto_approvals) == 3
    report = (root / "product" / "BUILD-REPORT.md").read_text()
    assert "plain-language" in report or "确认" in report
    # Three build commits exist beyond init.
    import subprocess

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=root, capture_output=True, text=True
    ).stdout
    assert log.count("feat(") == 3
