import shutil

import pytest

from autoproduct import testing as testing_mod
from autoproduct.upstream import (
    approve_spec,
    init_workspace,
    load_project,
    run_build,
    run_spec_stage,
)
from autoproduct.upstream.ears import classify, lint_criteria

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


# --- ears_lint ---------------------------------------------------------------

def test_ears_patterns_accepted():
    good = [
        "The system shall store items in memory.",
        "When a client POSTs /items, the system shall return the new id.",
        "While offline, the app shall queue writes locally.",
        "If the name is empty, then the system shall reject the request.",
        "Where subpackages are enabled, the app shall lazy-load them.",
    ]
    assert lint_criteria(good) == []
    assert classify(good[1]) == "event"


def test_ears_rejects_non_pattern_and_vague():
    issues = lint_criteria(["Items can be added quickly.", "The app shall be fast."])
    problems = " | ".join(i.problem for i in issues)
    assert "does not match any EARS pattern" in problems
    assert "vague term" in problems


# --- workspace ---------------------------------------------------------------

def test_init_workspace_seeds_profile_constraints(tmp_path):
    root = init_workspace(tmp_path / "shop", "shop", "miniprogram")
    project = load_project(root)
    assert project.profile == "miniprogram"
    claude = (root / "CLAUDE.md").read_text()
    assert "2MB" in claude and "隐私协议" in claude
    with pytest.raises(FileExistsError):
        init_workspace(root, "shop", "miniprogram")


def test_unknown_profile_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown profile"):
        init_workspace(tmp_path / "x", "x", "desktop")


# --- spec stage --------------------------------------------------------------

def test_spec_stage_produces_approvable_spec(tmp_path):
    root = init_workspace(tmp_path / "web", "web", "web")
    spec = run_spec_stage(root, "an item store API", provider="mock")
    assert spec.status == "proposed"
    assert spec.revisions == 0
    assert lint_criteria(spec.criteria) == []
    assert (root / "specs" / spec.slug / "spec.md").exists()


def test_spec_stage_revises_vague_first_draft(tmp_path):
    root = init_workspace(tmp_path / "web", "web", "web")
    spec = run_spec_stage(root, "an item store API, make it vague", provider="mock")
    # First draft had "shall be fast"; lint + critic majors forced a revision.
    assert spec.revisions >= 1
    assert spec.status == "proposed"
    assert all("fast" not in c for c in spec.criteria)


def test_build_refuses_unapproved_spec(tmp_path):
    root = init_workspace(tmp_path / "web", "web", "web")
    spec = run_spec_stage(root, "an item store API", provider="mock")
    result = run_build(root, spec.slug, provider="mock")
    assert result.status == "error"
    assert "Gate U3" in result.detail


# --- full greenfield flow ----------------------------------------------------

def test_end_to_end_init_spec_approve_build(tmp_path, monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import autoproduct.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)

    root = init_workspace(tmp_path / "web", "web", "web")
    spec = run_spec_stage(root, "an item store API", provider="mock")
    approve_spec(root, spec.slug)

    result = run_build(root, spec.slug, provider="mock")
    assert result.status == "built", result.detail
    assert result.commit
    assert "feature.py" in result.files_written
    assert (root / "tests" / "test_feature.py").exists()
    assert "2 passed" in result.test_summary

    # The build commit is a reviewable diff for the downstream stages.
    from autoproduct.orchestrator import run_review
    from pathlib import Path

    skills = str(Path(__file__).parent.parent / "skills")
    review, state = run_review(
        "HEAD~1", repo_dir=str(root), skills_dir=skills, provider_override="mock"
    )
    assert review is not None
    assert state["dor_pass"]
