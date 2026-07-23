from autoproduct.tools.symbols import symbol_refs
from autoproduct.tools.voter_tools import ToolBox


def _repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "utils.py").write_text(
        "def apply_discount(price, rate):\n    return price * (1 - rate)\n"
    )
    (tmp_path / "app" / "billing.py").write_text(
        "from app.utils import apply_discount\n\n"
        "def invoice(items):\n"
        "    return sum(apply_discount(i.price, i.rate) for i in items)\n"
    )
    (tmp_path / "app" / "docs.py").write_text(
        '# not a real reference:\nNOTE = "apply_discount is documented here"\n'
    )
    return tmp_path


def test_definitions_and_call_sites_found(tmp_path):
    out = symbol_refs(_repo(tmp_path), "apply_discount")
    assert "definitions:" in out
    assert "app/utils.py:1" in out
    assert "call sites:" in out
    assert "app/billing.py:4" in out


def test_string_mention_is_not_a_reference(tmp_path):
    out = symbol_refs(_repo(tmp_path), "apply_discount")
    # tree-sitter is structural: the docstring line is not a call site.
    call_section = out.split("call sites:")[1]
    assert "docs.py" not in call_section


def test_unknown_symbol(tmp_path):
    assert "no occurrences" in symbol_refs(_repo(tmp_path), "ghost_symbol")


def test_available_through_toolbox(tmp_path):
    box = ToolBox(_repo(tmp_path), ["symbol_refs"])
    out = box.call("symbol_refs", {"symbol": "apply_discount"})
    assert "app/billing.py:4" in out
    assert box.calls_made == 1
