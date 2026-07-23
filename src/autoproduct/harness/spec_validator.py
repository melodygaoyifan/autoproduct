"""SpecValidator — the harness component that makes voter specs load-bearing.

ADR-008/ADR-009 (doc 11): a voter skill is a markdown file whose YAML
frontmatter is a machine-checked contract. A skill that fails validation
does not register — there is no degraded mode and no warning-and-continue.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

# Tool risk ceiling for any voter-callable tool (§09.7.1): read-only and
# analysis tools only. L3/L4 do not exist as voter tools — structurally.
VOTER_RISK_CEILING = 2

KNOWN_PROVIDERS = {"anthropic", "openai", "google", "xai", "mock"}

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


class VoterSpecValidationError(Exception):
    pass


class VoterSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    description: str
    provider: str
    model: str
    taxonomy_slice: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    risk_ceiling: int = Field(default=0, ge=0, le=VOTER_RISK_CEILING)
    timeout_s: int = Field(default=120, gt=0)
    max_retries: int = Field(default=3, ge=0, le=3)


class LoadedSkill(BaseModel):
    spec: VoterSpec
    body: str
    path: str


class SpecValidator:
    def load(self, skill_path: str | Path) -> LoadedSkill:
        path = Path(skill_path)
        text = path.read_text(encoding="utf-8")
        match = _FRONTMATTER.match(text)
        if not match:
            raise VoterSpecValidationError(f"{path}: missing YAML frontmatter block")
        try:
            raw = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            raise VoterSpecValidationError(f"{path}: unparseable frontmatter: {exc}") from exc
        try:
            spec = VoterSpec.model_validate(raw)
        except ValidationError as exc:
            raise VoterSpecValidationError(f"{path}: invalid spec: {exc}") from exc
        if spec.provider not in KNOWN_PROVIDERS:
            raise VoterSpecValidationError(
                f"{path}: unknown provider {spec.provider!r} (known: {sorted(KNOWN_PROVIDERS)})"
            )
        body = match.group(2).strip()
        if not body:
            raise VoterSpecValidationError(f"{path}: skill body is empty")
        return LoadedSkill(spec=spec, body=body, path=str(path))
