import shutil

import pytest

from autoproduct import testing as testing_mod
from autoproduct.tools.wireup import wireup_check
from autoproduct.upstream import init_workspace
from autoproduct.upstream.autopilot import run_feature
from autoproduct.upstream.build import _write_files

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


# --- wireup_check ------------------------------------------------------------

def _app(tmp_path, frontend_call: str, backend_route: str | None):
    (tmp_path / "static").mkdir(parents=True, exist_ok=True)
    (tmp_path / "static" / "app.js").write_text(
        f"fetch('{frontend_call}').then(r => r.json())\n"
    )
    routes = f"@app.get(\"{backend_route}\")\ndef handler(): ...\n" if backend_route else ""
    (tmp_path / "main.py").write_text(f"app = App()\n{routes}")
    return tmp_path


def test_wireup_flags_missing_route(tmp_path):
    _app(tmp_path, "/api/items", "/api/other")
    report = wireup_check(tmp_path)
    assert any("no backend route" in f.title for f in report.findings)
    assert report.findings[0].taxonomy_hint == "P1"


def test_wireup_matches_path_params(tmp_path):
    _app(tmp_path, "/api/items/123", "/api/items/{item_id}")
    assert wireup_check(tmp_path).findings == []


def test_wireup_wx_request_and_template_params(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "order.js").write_text(
        "wx.request({ url: `/api/orders/${id}`, method: 'GET' })\n"
    )
    (tmp_path / "srv.py").write_text('@app.get("/api/orders/{oid}")\ndef get_order(): ...\n')
    assert wireup_check(tmp_path).findings == []


def test_wireup_silent_when_no_backend(tmp_path):
    (tmp_path / "app.js").write_text("fetch('/api/x')")
    report = wireup_check(tmp_path)
    assert report.findings == []
    assert "no recognizable backend" in report.detail


# --- test write-lock (§13.29.5) ----------------------------------------------

def test_existing_tests_are_read_only_to_implementer(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_old.py").write_text("def test_a():\n    assert True\n")
    with pytest.raises(ValueError, match="read-only"):
        _write_files(
            tmp_path,
            [{"path": "tests/test_old.py", "new_content": "def test_a():\n    pass\n"}],
            allowed_test_paths={"tests/test_new.py"},
        )
    # New skeleton paths are the legal test-authoring surface.
    written = _write_files(
        tmp_path,
        [{"path": "tests/test_new.py", "new_content": "def test_b():\n    assert 1\n"}],
        allowed_test_paths={"tests/test_new.py"},
    )
    assert written == ["tests/test_new.py"]


# --- per-feature FDR flow ----------------------------------------------------

FEATURE_FDR = "给接龙加一个功能：住户可以取消自己的订单。必须有：取消入口、取消后汇总更新。"


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import autoproduct.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def test_feature_fdr_builds_against_existing_product(tmp_path):
    root = init_workspace(tmp_path / "prod", "prod", "miniprogram")
    (root / "existing.py").write_text("# existing product code\n")
    fdr = root / "feature.md"
    fdr.write_text(FEATURE_FDR, encoding="utf-8")

    paused = run_feature(root, fdr, provider="mock", yes=False)
    assert paused.status == "awaiting_confirmation"
    feature_dirs = list((root / "product" / "features").iterdir())
    assert len(feature_dirs) == 1
    assert (feature_dirs[0] / "CONFIRMATION.md").exists()

    result = run_feature(root, fdr, provider="mock", yes=True)
    assert result.status == "completed", [o.model_dump() for o in result.outcomes]
    assert all(o.status == "built" for o in result.outcomes)
    # Feature artifacts are self-contained and granular.
    newest = sorted((root / "product" / "features").iterdir())[-1]
    assert (newest / "fdr.md").read_text() == FEATURE_FDR
    assert (newest / "REPORT.md").exists()
    # Existing product file untouched.
    assert (root / "existing.py").read_text() == "# existing product code\n"
