from __future__ import annotations

import os

from autoproduct.providers.base import Provider, ProviderError, register


@register
class AnthropicProvider(Provider):
    name = "anthropic"

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 4096) -> str:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )
