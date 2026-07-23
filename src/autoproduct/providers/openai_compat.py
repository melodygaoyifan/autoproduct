"""OpenAI-compatible chat-completions adapters (OpenAI, xAI).

Thin httpx clients rather than full SDKs — the voter only needs
single-turn text completion, and one adapter shape covers both providers.
"""

from __future__ import annotations

import os

import httpx

from autoproduct.providers.base import Provider, ProviderError, register


class _ChatCompletionsProvider(Provider):
    base_url: str
    api_key_env: str

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 4096) -> str:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ProviderError(f"{self.api_key_env} is not set")
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


@register
class OpenAIProvider(_ChatCompletionsProvider):
    name = "openai"
    base_url = "https://api.openai.com/v1"
    api_key_env = "OPENAI_API_KEY"


@register
class XAIProvider(_ChatCompletionsProvider):
    name = "xai"
    base_url = "https://api.x.ai/v1"
    api_key_env = "XAI_API_KEY"
