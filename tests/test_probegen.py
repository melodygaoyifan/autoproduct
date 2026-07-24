"""Auto-generated probes: from the founder's PRD against the ACTUAL built
product — the fixtures in benchmarks/ are labeled regression cases; real
users get generation."""

import shutil
import subprocess

import pytest

from autoproduct.upstream import init_workspace
from autoproduct.upstream.probegen import generate_probes, verify_product

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

# A tiny real stdlib product: one route, JSON 200 on "/".
MAIN_PY = """import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", int(os.environ.get("PORT", 8646))), Handler).serve_forever()
"""


def _workspace_with_product(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    (root / "FDR.md").write_text("一个能打开的首页。必须有：首页能打开。")
    (root / "main.py").write_text(MAIN_PY)
    # A built spec so criteria exist, and an observable route for probegen.
    spec_dir = root / "specs" / "home"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.yaml").write_text(
        "slug: home\ntitle: Home\nstatus: approved\nrequest: 首页\nprofile: web\n"
        "design: main.py\ncriteria: ['The system shall serve the home page.']\n"
        "test_skeletons: []\nbuilt: true\n"
    )
    (root / "routes.py").write_text('@app.get("/")\ndef home(): ...\n')
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def test_generation_guardrails_drop_invalid_bodies(tmp_path):
    root = _workspace_with_product(tmp_path)
    probes, notes = generate_probes(root, provider="mock")
    # Mock emits one valid probe and one non-parsing body.
    assert [p.name for p in probes] == ["root-responds"]
    assert any("does not parse" in n for n in notes)
    assert "wait()" in probes[0].script and "proc.terminate()" in probes[0].script


def test_verify_runs_generated_probes_against_booted_product(tmp_path):
    root = _workspace_with_product(tmp_path)
    path = verify_product(root, provider="mock")
    text = path.read_text()
    assert "✅ root-responds" in text          # generated probe passed live
    assert "1/1" in text
    assert "⚠️" in text                        # the dropped body is visible


def test_no_routes_is_a_visible_note_not_a_pass(tmp_path):
    root = init_workspace(tmp_path / "empty", "e", "web")
    (root / "FDR.md").write_text("东西")
    probes, notes = generate_probes(root, provider="mock")
    assert probes == []
    assert any("no observed backend routes" in n for n in notes)
