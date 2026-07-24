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

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
    ) -> str:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ProviderError(f"{self.api_key_env} is not set")
        # gpt-5 / o-series reject the legacy max_tokens param; older models
        # and most compatible endpoints still require it. Send the modern
        # name for models that demand it, retry once on the 400 that says
        # the other name is unsupported.
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        modern = model.startswith(("gpt-5", "o1", "o3", "o4"))
        params = (
            ("max_completion_tokens", "max_tokens") if modern
            else ("max_tokens", "max_completion_tokens")
        )
        response = None
        for attempt in params:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={**payload, attempt: max_tokens},
                timeout=120,
            )
            if response.status_code == 400 and "max_tokens" in response.text:
                continue
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        response.raise_for_status()
        raise ProviderError(f"{self.name}: both token params rejected for {model}")


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
