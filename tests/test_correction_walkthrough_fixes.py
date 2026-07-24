"""Shakedown fixes: correction repairs iterate against the suite;
walkthrough holds its deterministic floor PER BATCH."""

import shutil
import subprocess

import pytest
import yaml as yaml_lib

from autoproduct import testing as testing_mod
from autoproduct.providers.base import Provider, register
from autoproduct.upstream import init_workspace
from autoproduct.upstream.autopilot import run_autopilot
from autoproduct.upstream.correction import run_correction
from autoproduct.upstream.walkthrough import generate_walkthrough

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import autoproduct.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def _built_workspace(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    (root / "FDR.md").write_text("团长发起接龙，住户下单，团长看汇总。必须有：发起、下单、汇总。")
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)
    assert result.status == "completed"
    return root


@register
class TwoAttemptRepairer(Provider):
    """Routes like mock, then: attempt 1 breaks the suite, attempt 2 (with
    <test_failure> feedback) repairs correctly."""

    name = "two_attempt_repairer"

    def chat(self, *, model, system, messages, max_tokens=4096):
        from autoproduct.providers.mock import MockProvider
        from autoproduct.upstream.correction import CORRECTION_MARKER

        user = messages[0]["content"]
        if CORRECTION_MARKER in system:
            return MockProvider().chat(model=model, system=system, messages=messages)
        if "repairing ONE founder complaint" in system:
            import re

            match = re.search(
                r'<(?:existing|current)_file path="([^"]+)">\n(.*?)\n</(?:existing|current)_file>',
                user, re.DOTALL,
            )
            path = match.group(1) if match else "feature_t1.py"
            if "<test_failure" not in user:
                return yaml_lib.safe_dump(
                    {"files": [{"path": path, "new_content": "this breaks ( everything\n"}]}
                )
            good = match.group(2) if match else ""
            # Restore working content (strip the breakage) + the fix marker.
            restored = (
                good if "class ItemStore" in good
                else "class ItemStore:\n    def __init__(self):\n        self._items = []\n\n"
                     "    def add(self, name):\n"
                     "        if not name:\n            raise ValueError('name required')\n"
                     "        item_id = len(self._items) + 1\n"
                     "        self._items.append({'id': item_id, 'name': name})\n"
                     "        return item_id\n\n"
                     "    def list_items(self):\n        return list(reversed(self._items))\n"
            )
            return yaml_lib.safe_dump(
                {"files": [{"path": path, "new_content": restored + "\n# repaired on attempt 2\n"}]}
            )
        return MockProvider().chat(model=model, system=system, messages=messages)


def test_correction_repair_iterates_to_success(tmp_path):
    root = _built_workspace(tmp_path)
    result = run_correction(root, "按钮文字不对", provider="two_attempt_repairer")
    assert result.status == "fixed", result.detail
    assert "2 attempt(s)" in result.detail
    show = subprocess.run(["git", "show", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout
    assert "repaired on attempt 2" in show


@register
class AlwaysBreakingRepairer(TwoAttemptRepairer):
    name = "always_breaking_repairer"

    def chat(self, *, model, system, messages, max_tokens=4096):
        if "repairing ONE founder complaint" in system:
            return yaml_lib.safe_dump(
                {"files": [{"path": "feature_t1.py", "new_content": "broken (\n"}]}
            )
        return super().chat(model=model, system=system, messages=messages)


def test_correction_reverts_after_exhausted_attempts(tmp_path):
    root = _built_workspace(tmp_path)
    before = (root / "feature_t1.py").read_text()
    result = run_correction(root, "按钮文字不对", provider="always_breaking_repairer")
    assert result.status == "error"
    assert "after 3 attempt(s)" in result.detail
    assert (root / "feature_t1.py").read_text() == before  # fully reverted


# --- walkthrough per-batch floor ----------------------------------------------

@register
class FaithfulRenderer(Provider):
    """Renders every criterion as one plain-language checkbox."""

    name = "faithful_renderer"

    def chat(self, *, model, system, messages, max_tokens=4096):
        criteria = yaml_lib.safe_load(messages[0]["content"].split("<language_sample>")[0])
        return "\n".join(f"- [ ] 说人话STEP: {c['criterion'][:40]}" for c in criteria)


def _many_criteria_workspace(tmp_path, n=20):
    root = init_workspace(tmp_path / "w", "w", "web")
    spec_dir = root / "specs" / "big"
    spec_dir.mkdir(parents=True)
    criteria = [f"The system shall do thing number {i}." for i in range(n)]
    spec_dir.joinpath("spec.yaml").write_text(
        yaml_lib.safe_dump(
            {"slug": "big", "title": "Big", "status": "approved", "request": "r",
             "profile": "web", "design": "d", "criteria": criteria,
             "test_skeletons": [], "built": True},
            sort_keys=False,
        )
    )
    return root, n


def test_walkthrough_batches_keep_plain_language_on_large_sets(tmp_path):
    root, n = _many_criteria_workspace(tmp_path)
    path = generate_walkthrough(root, provider="faithful_renderer")
    text = path.read_text()
    assert text.count("[ ]") >= n              # floor: every criterion present
    assert text.count("说人话STEP") >= n        # AND plain language everywhere


def test_walkthrough_mixed_fallback_per_batch(tmp_path):
    # Mock provider returns junk for walkthrough batches -> every batch falls
    # back deterministically, still covering all criteria.
    root, n = _many_criteria_workspace(tmp_path)
    path = generate_walkthrough(root, provider="mock")
    text = path.read_text()
    assert text.count("[ ]") == n
    assert "The system shall do thing number 0." in text
