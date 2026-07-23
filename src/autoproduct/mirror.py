"""YAML mirror (§09.6) — a human-readable audit trail written at every
super-step, independent of the checkpointer. `autoproduct replay` (later
milestone) reads these files back."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import yaml


class YamlMirror:
    def __init__(self, base_dir: str | Path, review_id: str):
        self.dir = Path(base_dir) / review_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._step = 0

    def write(self, node: str, payload: dict[str, Any]) -> Path:
        self._step += 1
        path = self.dir / f"{self._step:02d}-{node}.yaml"
        record = {
            "node": node,
            "step": self._step,
            "written_at": datetime.datetime.now(datetime.UTC).isoformat(),
            **payload,
        }
        path.write_text(
            yaml.safe_dump(record, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return path
