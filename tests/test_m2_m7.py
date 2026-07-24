import shutil
import subprocess

import pytest
import yaml

from autoproduct import testing as testing_mod
from autoproduct.upstream import init_workspace
from autoproduct.upstream.autopilot import (
    estimate_hint,
    run_autopilot,
    tag_checkpoint,
    undo_last,
)
from autoproduct.upstream.blocks import blocks_context, catalog_summary, matching_blocks
from autoproduct.upstream.correction import run_correction
from autoproduct.upstream.telemetry import generate_digest, install_telemetry, read_events
from autoproduct.upstream.walkthrough import built_criteria, generate_walkthrough

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

GOOD_FDR = "团长发起接龙，住户下单，团长看汇总。必须有：发起、下单、汇总。"


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import autoproduct.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def _built_workspace(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    (root / "FDR.md").write_text(GOOD_FDR, encoding="utf-8")
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)
    assert result.status == "completed"
    return root


# --- M2: design baseline + screenshots (gated) --------------------------------

def test_design_baselines_written_and_constrained(tmp_path):
    web = init_workspace(tmp_path / "w", "w", "web")
    assert (web / "static" / "baseline.css").exists()
    assert "baseline.css" in (web / "CLAUDE.md").read_text()
    mini = init_workspace(tmp_path / "m", "m", "miniprogram")
    assert (mini / "styles" / "baseline.wxss").exists()
    assert "WeUI" in (mini / "CLAUDE.md").read_text()


def test_screenshots_gated_visibly(tmp_path):
    from autoproduct.upstream.screenshots import capture

    root = init_workspace(tmp_path / "w", "w", "web")
    result = capture(root, "web")
    # No playwright in the dev env: visible note, never a silent skip.
    assert result.captured == [] and "playwright" in result.note or result.captured


# --- M4: walkthrough -----------------------------------------------------------

def test_walkthrough_covers_every_built_criterion(tmp_path):
    root = _built_workspace(tmp_path)
    rows = built_criteria(root)
    assert rows  # three built specs × two criteria
    path = root / "product" / "ACCEPTANCE.md"
    assert path.exists()  # written automatically post-build
    text = path.read_text()
    assert text.count("[ ]") >= len(rows)  # deterministic floor held


# --- M3: correction loop --------------------------------------------------------

def test_correction_fix_path_commits_repair(tmp_path):
    root = _built_workspace(tmp_path)
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                            capture_output=True, text=True).stdout
    result = run_correction(root, "按钮文字不对，应该是「参加接龙」", provider="mock")
    assert result.status == "fixed", result.detail
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                           capture_output=True, text=True).stdout
    assert before != after
    assert "corrected per founder" in subprocess.run(
        ["git", "show", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout


def test_correction_scope_change_raises_approved_scr(tmp_path):
    root = _built_workspace(tmp_path)
    result = run_correction(root, "新增：住户可以取消订单", provider="mock")
    assert result.status == "scr_raised"
    scr = yaml.safe_load(
        next((root / ".mas" / "scr").glob("SCR-*.yaml")).read_text()
    )
    assert scr["status"] == "approved"
    assert "founder correction" in scr["reason"]


# --- M5: telemetry + digest -----------------------------------------------------

def test_telemetry_installed_and_digest_reconciles(tmp_path):
    root = _built_workspace(tmp_path)
    assert (root / "telemetry.py").exists()  # installed post-build
    # A user's product records events…
    subprocess.run(
        ["python3", "-c",
         "import telemetry; telemetry.track('order_created'); "
         "telemetry.track('order_created'); telemetry.track('groupbuy_created')"],
        cwd=root, check=True,
    )
    counts = read_events(root)
    assert counts["order_created"] == 2
    path = generate_digest(root, provider="mock")
    assert path.exists() and "digest" in path.read_text()


# --- M6: blocks -----------------------------------------------------------------

def test_blocks_match_and_carry_contract():
    assert matching_blocks("miniprogram", "用户下单后需要微信支付收款") == [
        "miniprogram/wxpay.js"
    ]
    context = blocks_context("miniprogram", "需要微信支付")
    assert "requestPayment" in context and "never" in context
    assert "wxpay" in catalog_summary("miniprogram")
    assert matching_blocks("web", "用户要登录注册") == ["web/auth.py"]


# --- M7: estimates + undo -------------------------------------------------------

def test_estimate_hint_in_confirmation(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    (root / "FDR.md").write_text(GOOD_FDR, encoding="utf-8")
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=False)
    assert "预计" in result.confirmation
    assert "重试" in result.confirmation or "retried" in result.confirmation


def test_undo_restores_previous_checkpoint(tmp_path):
    root = _built_workspace(tmp_path)  # checkpoint 001 tagged on completion
    marker = root / "extra.txt"
    marker.write_text("later change")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "later"], cwd=root, check=True)
    tag_checkpoint(root)  # checkpoint 002
    result = undo_last(root)
    assert result["status"] == "undone"
    assert not marker.exists()  # back to checkpoint 001
    assert result["rescue_branch"].startswith("rescue/")  # undo is undoable


def test_undo_with_single_checkpoint_is_safe(tmp_path):
    root = _built_workspace(tmp_path)
    assert undo_last(root)["status"] == "nothing_to_undo"
