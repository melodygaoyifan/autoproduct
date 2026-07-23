"""Tolerant YAML extraction from LLM responses.

Models narrate despite instructions — especially on large diffs. The
envelope contract is enforced on the *extracted* mapping, not on the raw
response: we try, in order, any fenced block, the raw text, and the text
from the first expected top-level key onward.
"""

from __future__ import annotations

import re

import yaml

_FENCE = re.compile(r"```(?:yaml|yml)?\s*\n(.*?)```", re.DOTALL)


def extract_mapping(raw: str, expected_keys: tuple[str, ...]) -> dict:
    text = raw.strip()
    candidates: list[str] = []
    for match in _FENCE.finditer(text):
        candidates.append(match.group(1))
    candidates.append(text.strip("`"))
    key_match = re.search(
        rf"^({'|'.join(map(re.escape, expected_keys))}):", text, re.MULTILINE
    )
    if key_match:
        candidates.append(text[key_match.start() :])

    for candidate in candidates:
        try:
            data = yaml.safe_load(candidate)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and any(k in data for k in expected_keys):
            return data
    error = ValueError(
        f"no YAML mapping with any of {expected_keys} found in response "
        f"({len(raw)} chars)"
    )
    error.raw_snippet = raw  # surfaced in failure notes for debugging
    raise error
