"""Regression tests for the failure dogfooding found on PR #1: voters
narrating prose around their YAML envelope on large diffs."""

import pytest

from autoproduct.yamlx import extract_mapping

CLEAN = "status: OK\nfindings: []\n"

PROSE_PREFIX = """I reviewed the diff carefully. Here is my assessment:

status: OK
findings: []
"""

FENCED_WITH_PROSE = """Let me summarize what I found.

```yaml
status: OK
findings:
  - title: x
    severity: high
```

That's everything.
"""


def test_clean_yaml():
    assert extract_mapping(CLEAN, ("status",))["status"] == "OK"


def test_prose_before_yaml():
    assert extract_mapping(PROSE_PREFIX, ("status",))["status"] == "OK"


def test_fenced_block_surrounded_by_prose():
    data = extract_mapping(FENCED_WITH_PROSE, ("status",))
    assert data["findings"][0]["title"] == "x"


def test_tool_request_with_narration():
    raw = "I need to check the callers first.\n\ntool_request:\n  tool: grep\n  args: {pattern: x}\n"
    assert extract_mapping(raw, ("tool_request",))["tool_request"]["tool"] == "grep"


def test_no_mapping_raises():
    with pytest.raises(ValueError, match="no YAML mapping"):
        extract_mapping("just prose, no yaml at all", ("status",))
