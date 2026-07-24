import shutil

import pytest

from autoproduct.upstream import init_workspace
from autoproduct.upstream.provisioning import auto_provision_cloud
from autoproduct.upstream.ship import push_web, setup_miniprogram_tests

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


def test_cloud_autoprovision_gates_visibly(tmp_path, monkeypatch):
    root = init_workspace(tmp_path / "w", "w", "web")
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = auto_provision_cloud(root, "web")
    assert result["status"] == "unavailable"
    assert "SERVICES.md still works" in result["detail"]  # guided path remains


def test_miniprogram_cloud_is_guided_only(tmp_path):
    root = init_workspace(tmp_path / "m", "m", "miniprogram")
    result = auto_provision_cloud(root, "miniprogram")
    assert result["status"] == "guided_only"


def test_push_web_gates_on_cli_and_login(tmp_path, monkeypatch):
    root = init_workspace(tmp_path / "w", "w", "web")
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = push_web(root)
    assert result["status"] == "unavailable"
    assert "railway" in result["detail"]


def test_setup_tests_writes_scaffold(tmp_path, monkeypatch):
    root = init_workspace(tmp_path / "m", "m", "miniprogram")

    # Hermetic: skip the real npm install, verify the scaffold contract.
    import subprocess as sp

    real_run = sp.run

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["npm", "install"]:
            return sp.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr("subprocess.run", fake_run)
    result = setup_miniprogram_tests(root)
    assert result["status"] == "ready"
    import json

    package = json.loads((root / "package.json").read_text())
    assert package["scripts"]["test"] == "jest"
    assert "miniprogram-simulate" in package["devDependencies"]
    assert (root / "tests" / "pages.test.js").exists()
