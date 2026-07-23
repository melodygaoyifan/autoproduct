import shutil
import subprocess

import pytest
import yaml

from autoproduct import testing as testing_mod
from autoproduct.testing import combine_reports, run_js_tests
from autoproduct.upstream import init_workspace
from autoproduct.upstream.autopilot import run_autopilot, schedule_waves
from autoproduct.upstream.plan import Task
from autoproduct.upstream.provisioning import (
    preview_env,
    provision_local,
    services_context,
    write_cloud_guide,
)
from autoproduct.upstream.ship import ship

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import autoproduct.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


# --- provisioning ------------------------------------------------------------

def test_local_provisioning_and_context(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    provision_local(root)
    services = yaml.safe_load((root / ".mas" / "services.yaml").read_text())
    assert services["database"]["kind"] == "sqlite"
    context = services_context(root)
    assert "sqlite3" in context and "never an in-memory store" in context
    env = preview_env(root)
    assert env["DATABASE_PATH"].endswith("data/app.db")


def test_cloud_guide_and_secrets_never_leak(tmp_path):
    root = init_workspace(tmp_path / "m", "m", "miniprogram")
    provision_local(root)
    write_cloud_guide(root, "miniprogram")
    assert "微信云开发" in (root / "SERVICES.md").read_text()
    # A real credential in the vault flows to preview env but NEVER to the
    # implementer-visible context.
    (root / ".mas" / "secrets.yaml").write_text("WX_CLOUD_ENV: prod-abc123\n")
    assert preview_env(root)["WX_CLOUD_ENV"] == "prod-abc123"
    assert "prod-abc123" not in services_context(root)


# --- JS test runner ----------------------------------------------------------

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


@node
def test_js_tests_gate_pass_and_fail(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "sum.test.js").write_text(
        "const t = require('node:test'); const assert = require('node:assert');\n"
        "t.test('sum', () => assert.strictEqual(1 + 2, 3));\n"
    )
    report = run_js_tests(tmp_path)
    assert report.status == "passed"
    (tmp_path / "tests" / "sum.test.js").write_text(
        "const t = require('node:test'); const assert = require('node:assert');\n"
        "t.test('sum', () => assert.strictEqual(1 + 2, 4));\n"
    )
    assert run_js_tests(tmp_path).status == "failed"


def test_js_absent_surface_is_none(tmp_path):
    assert run_js_tests(tmp_path) is None


@node
def test_combined_gate_fails_if_either_side_fails(tmp_path):
    from autoproduct.testing import TestReport

    js_fail = TestReport(status="failed", summary="1 failing")
    py_pass = TestReport(status="passed", summary="ok")
    assert combine_reports(py_pass, js_fail).status == "failed"
    assert combine_reports(py_pass, None).status == "passed"


# --- ship --------------------------------------------------------------------

def test_ship_web_writes_dockerfile_and_guide(tmp_path):
    root = init_workspace(tmp_path / "w", "w", "web")
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("print('hi')\n")
    guide = ship(root)
    assert (root / "Dockerfile").read_text().startswith("FROM python:3.12-slim")
    assert "railway" in guide.read_text() or "flyctl" in guide.read_text()


def test_ship_miniprogram_writes_project_config(tmp_path):
    root = init_workspace(tmp_path / "m", "m", "miniprogram")
    guide = ship(root)
    assert "AppID" in (root / "project.config.json").read_text()
    assert "微信开发者工具" in guide.read_text()


# --- parallel lanes ----------------------------------------------------------

def test_schedule_waves_one_per_lane():
    tasks = [
        Task(id="a", title="a", lane="api", estimate_hours=1),
        Task(id="b", title="b", lane="ui", estimate_hours=1),
        Task(id="c", title="c", lane="api", estimate_hours=1),
        Task(id="d", title="d", lane="api", estimate_hours=1, depends_on=["a", "b"]),
    ]
    waves = schedule_waves(tasks)
    assert [sorted(t.id for t in w) for w in waves] == [["a", "b"], ["c"], ["d"]]


def test_parallel_autopilot_builds_wave_in_worktrees(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    (root / "FDR.md").write_text(
        "团长发起接龙，住户下单。parallel plan。必须有：发起、下单。"
    )
    result = run_autopilot(
        root, root / "FDR.md", provider="mock", yes=True, parallel=True
    )
    assert result.status == "completed", [o.model_dump() for o in result.outcomes]
    assert {o.task_id for o in result.outcomes} == {"t1", "t2", "t3"}
    assert all(o.status == "built" for o in result.outcomes)
    # Wave 1 (t1 api + t2 ui) merged via --no-ff merge commits.
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=root, capture_output=True, text=True
    ).stdout
    assert log.count("merge build/") >= 3
    assert (root / "feature_t1.py").exists() and (root / "feature_t2.py").exists()
