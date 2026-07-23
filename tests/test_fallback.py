from autoproduct.harness import SpecValidator
from autoproduct.state import VoterStatus
from autoproduct.voters.base import Voter

DIFF = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,1 +1,2 @@
+    except Exception: pass
"""


def _skill(tmp_path, frontmatter_extra: str = ""):
    path = tmp_path / "probe.md"
    path.write_text(
        "---\nname: probe\ndescription: d\nprovider: openai\nmodel: gpt-5.4\n"
        f"max_retries: 0\n{frontmatter_extra}---\nFind bugs.\n"
    )
    return SpecValidator().load(path)


def test_missing_key_with_fallback_substitutes_visibly(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill = _skill(tmp_path, "fallback:\n  provider: mock\n  model: mock-1\n")
    output = Voter(skill).run(DIFF)
    assert output.status is VoterStatus.OK
    assert output.model == "mock-1"
    assert output.substituted_from is not None
    assert "openai/gpt-5.4" in output.substituted_from


def test_missing_key_without_fallback_blocks(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill = _skill(tmp_path)
    output = Voter(skill).run(DIFF)
    assert output.status is VoterStatus.BLOCKED_TOOL_FAILURE
    assert "OPENAI_API_KEY" in output.notes
