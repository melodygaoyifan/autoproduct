import shutil

import pytest
import yaml

from autoproduct import testing as testing_mod
from autoproduct.tools.integrity import assertion_delta
from autoproduct.upstream import init_workspace, run_spec_stage
from autoproduct.upstream.plan import Task, budget_check, lane_check
from autoproduct.upstream.spec import approve_scr, load_spec, raise_scr

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


# --- assertion_delta ---------------------------------------------------------

BEFORE = "def test_a():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n"


def test_removed_assert_detected():
    after = "def test_a():\n    assert add(1, 2) == 3\n"
    changes = assertion_delta(BEFORE, after)
    assert [c.change for c in changes] == ["removed_assert"]
    assert "add(0, 0)" in changes[0].node


def test_added_skip_detected():
    after = "import pytest\n@pytest.mark.skip\ndef test_a():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n"
    assert any(c.change == "added_skip" for c in assertion_delta(BEFORE, after))


def test_strengthening_is_fine():
    after = BEFORE + "    assert add(-1, 1) == 0\n"
    assert assertion_delta(BEFORE, after) == []


def test_weakened_skeleton_rejected_at_write(tmp_path):
    from autoproduct.upstream.build import _write_files

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_s.py").write_text(BEFORE)
    with pytest.raises(ValueError, match="test weakening"):
        _write_files(
            tmp_path,
            [{"path": "tests/test_s.py", "new_content": "def test_a():\n    pass\n"}],
            allowed_test_paths={"tests/test_s.py"},
        )


# --- lane_check / budget_check ----------------------------------------------

def test_lane_collision_detected():
    tasks = [
        Task(id="a", title="a", lane="api", estimate_hours=2, files_expected=["app/orders.py"]),
        Task(id="b", title="b", lane="ui", estimate_hours=2, files_expected=["app/orders.py"]),
    ]
    assert any("lane collision" in i for i in lane_check(tasks))


def test_same_lane_overlap_allowed():
    tasks = [
        Task(id="a", title="a", lane="api", estimate_hours=2, files_expected=["app/x.py"]),
        Task(id="b", title="b", lane="api", estimate_hours=2, files_expected=["app/x.py"]),
    ]
    assert lane_check(tasks) == []


def test_budget_check():
    tasks = [Task(id="a", title="a", estimate_hours=30), Task(id="b", title="b", estimate_hours=20)]
    assert budget_check(tasks, 40)
    assert budget_check(tasks, 60) == []


# --- SCR channel -------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import autoproduct.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def _built_spec(tmp_path):
    from autoproduct.upstream import approve_spec, run_build

    root = init_workspace(tmp_path / "p", "p", "web")
    spec = run_spec_stage(root, "an item store API", provider="mock")
    approve_spec(root, spec.slug)
    result = run_build(root, spec.slug, provider="mock")
    assert result.status == "built"
    return root, spec.slug


def test_built_spec_is_frozen_without_scr(tmp_path):
    root, slug = _built_spec(tmp_path)
    with pytest.raises(PermissionError, match="requires an approved SCR"):
        run_spec_stage(root, "an item store API", provider="mock")


def test_approved_scr_grants_one_regeneration(tmp_path):
    from autoproduct.upstream import approve_spec, run_build

    root, slug = _built_spec(tmp_path)
    raise_scr(root, slug, "criteria missed the empty-name case")
    approve_scr(root, 1)
    regenerated = run_spec_stage(root, "an item store API", provider="mock")
    assert regenerated.status == "proposed"
    assert regenerated.built is False  # freeze re-arms after the rebuild
    approve_spec(root, regenerated.slug)
    rebuilt = run_build(root, regenerated.slug, provider="mock")
    assert rebuilt.status == "built"
    # Rebuilt -> frozen again; the consumed SCR grants nothing further.
    with pytest.raises(PermissionError):
        run_spec_stage(root, "an item store API", provider="mock")


# --- design memory + changelog ----------------------------------------------

def test_build_appends_design_memory_and_changelog(tmp_path):
    root, slug = _built_spec(tmp_path)
    design = (root / "product" / "design.md").read_text()
    assert "Item store API" in design and "feature.py" in design
    fragment = (root / "product" / "changelog" / f"{slug}.md").read_text()
    assert "acceptance criteria" in fragment
    # Next spec sees the architecture memory (mock ignores it, but the
    # plumbing must carry it) — just verify no crash and file intact.
    spec = load_spec(root, slug)
    assert spec.built is True
