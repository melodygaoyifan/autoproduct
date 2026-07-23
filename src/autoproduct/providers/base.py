"""Provider adapters — Layer 1 of the stack (§08.3.1).

Voters name a model family in their spec frontmatter; the registry maps the
family to an adapter. Heterogeneity is the default posture (Principle 4);
running everything on one family is allowed only during bootstrap and is
surfaced in the YAML mirror so the substitution is visible, not silent.
"""

from __future__ import annotations

import abc


class ProviderError(RuntimeError):
    pass


class Provider(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 4096) -> str:
        """Single-turn completion returning the raw text response."""


_REGISTRY: dict[str, type[Provider]] = {}


def register(cls: type[Provider]) -> type[Provider]:
    _REGISTRY[cls.name] = cls
    return cls


def get_provider(name: str) -> Provider:
    # Imports deferred so an SDK missing for an unused provider never breaks startup.
    from autoproduct.providers import anthropic_provider, mock  # noqa: F401

    if name not in _REGISTRY:
        raise ProviderError(
            f"unknown provider {name!r}; available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]()
