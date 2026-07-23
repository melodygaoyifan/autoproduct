import pytest
import yaml

from autoproduct.harness import SpecValidator, VoterSpecValidationError
from autoproduct.providers.base import Provider, register
from autoproduct.state import VoterStatus
from autoproduct.tools.voter_tools import ToolBox
from autoproduct.voters.base import Voter

# --- ToolBox ----------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "utils.py").write_text(
        "def helper(a, b):\n    return a + b\n"
    )
    (tmp_path / "app" / "orders.py").write_text(
        "from app.utils import helper\n\ntotal = helper(1, 2)\n"
    )
    return tmp_path


def test_read_file_numbers_lines(repo):
    box = ToolBox(repo, ["read_file"])
    out = box.call("read_file", {"path": "app/utils.py"})
    assert out.startswith("1\tdef helper(a, b):")


def test_path_traversal_blocked(repo):
    box = ToolBox(repo, ["read_file"])
    out = box.call("read_file", {"path": "../../etc/passwd"})
    assert "escapes the repository root" in out


def test_grep_finds_callers(repo):
    box = ToolBox(repo, ["grep"])
    out = box.call("grep", {"pattern": r"helper\("})
    assert "app/orders.py:3" in out
    assert "app/utils.py:1" in out


def test_allowlist_enforced(repo):
    box = ToolBox(repo, ["read_file"])
    out = box.call("grep", {"pattern": "x"})
    assert "not in your allowlist" in out


def test_budget_enforced(repo):
    from autoproduct.tools.voter_tools import ToolBudgetExceeded

    box = ToolBox(repo, ["read_file"], budget=1)
    box.call("read_file", {"path": "app/utils.py"})
    with pytest.raises(ToolBudgetExceeded):
        box.call("read_file", {"path": "app/utils.py"})


# --- Voter investigation loop -------------------------------------------------


@register
class ScriptedProvider(Provider):
    """Replays a canned conversation: first requests a grep, then reports a
    finding grounded in the tool result it received."""

    name = "scripted"
    transcript: list[list[dict]] = []

    def chat(self, *, model, system, messages, max_tokens=4096):
        ScriptedProvider.transcript.append(messages)
        if len(messages) == 1:
            return yaml.safe_dump(
                {"tool_request": {"tool": "grep", "args": {"pattern": r"helper\("}}}
            )
        assert "<tool_result" in messages[-1]["content"]
        return yaml.safe_dump(
            {
                "status": "OK",
                "findings": [
                    {
                        "title": "Caller not updated for new signature",
                        "severity": "high",
                        "confidence": "certain",
                        "file_path": "app/orders.py",
                        "line_start": 3,
                        "line_end": 3,
                        "evidence": "total = helper(1, 2)",
                        "explanation": "grep showed a caller outside the diff",
                        "taxonomy_hint": "P8",
                    }
                ],
            }
        )


def _tooled_skill(tmp_path):
    path = tmp_path / "tooled.md"
    path.write_text(
        "---\nname: tooled\ndescription: d\nprovider: mock\nmodel: m\n"
        "tools: [grep, read_file]\ntool_budget: 3\n---\nInvestigate then judge.\n"
    )
    return SpecValidator().load(path)


def test_voter_tool_roundtrip(repo, tmp_path):
    ScriptedProvider.transcript = []
    voter = Voter(_tooled_skill(tmp_path), provider_override="scripted")
    output = voter.run("(diff)", repo_dir=str(repo))
    assert output.status is VoterStatus.OK
    assert output.findings[0].file_path == "app/orders.py"
    # Second turn carried the actual grep hits back to the model.
    final_messages = ScriptedProvider.transcript[-1]
    assert "app/orders.py:3" in final_messages[-1]["content"]


@register
class FlakyEmptyProvider(Provider):
    """First reply empty, then a valid envelope after the nudge."""

    name = "flaky_empty"
    calls = 0

    def chat(self, *, model, system, messages, max_tokens=4096):
        FlakyEmptyProvider.calls += 1
        if FlakyEmptyProvider.calls == 1:
            return "   "
        assert "previous reply was empty" in messages[-1]["content"]
        return yaml.safe_dump({"status": "OK", "findings": []})


def test_empty_response_nudged_once_then_recovers(tmp_path):
    FlakyEmptyProvider.calls = 0
    path = tmp_path / "plain.md"
    path.write_text(
        "---\nname: plain\ndescription: d\nprovider: mock\nmodel: m\n---\nJudge.\n"
    )
    voter = Voter(SpecValidator().load(path), provider_override="flaky_empty")
    output = voter.run("(diff)")
    assert output.status is VoterStatus.OK
    assert FlakyEmptyProvider.calls == 2


def test_unknown_tool_in_spec_rejected(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text(
        "---\nname: bad\ndescription: d\nprovider: mock\nmodel: m\n"
        "tools: [run_shell]\n---\nbody\n"
    )
    with pytest.raises(VoterSpecValidationError, match="unknown tools"):
        SpecValidator().load(path)
