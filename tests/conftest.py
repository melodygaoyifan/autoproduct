from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SKILLS = Path(__file__).parent.parent / "skills"


@pytest.fixture
def planted_diff_text() -> str:
    return (FIXTURES / "planted_bugs.diff").read_text()


@pytest.fixture
def skills_dir() -> str:
    return str(SKILLS)
