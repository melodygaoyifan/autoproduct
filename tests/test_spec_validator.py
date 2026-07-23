import pytest

from autoproduct.harness import SpecValidator, VoterSpecValidationError


def test_loads_valid_skill(skills_dir):
    skill = SpecValidator().load(f"{skills_dir}/correctness.md")
    assert skill.spec.name == "correctness"
    assert skill.spec.provider == "anthropic"
    assert "P9" in skill.spec.taxonomy_slice
    assert skill.body


def test_missing_frontmatter_rejected(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("# no frontmatter here")
    with pytest.raises(VoterSpecValidationError, match="frontmatter"):
        SpecValidator().load(bad)


def test_risk_ceiling_enforced(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nname: sneaky\ndescription: d\nprovider: mock\nmodel: m\n"
        "risk_ceiling: 4\n---\nbody"
    )
    with pytest.raises(VoterSpecValidationError, match="invalid spec"):
        SpecValidator().load(bad)


def test_empty_body_rejected(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("---\nname: hollow\ndescription: d\nprovider: mock\nmodel: m\n---\n")
    with pytest.raises(VoterSpecValidationError, match="body is empty"):
        SpecValidator().load(bad)
