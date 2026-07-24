from __future__ import annotations

import os

from autoproduct.providers.base import Provider, ProviderError, register


@register
class AnthropicProvider(Provider):
    name = "anthropic"

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
    ) -> str:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        import time

        import anthropic

        client = anthropic.Anthropic()
        # Transient-error resilience at the ADAPTER layer: overload/rate
        # limits retry with backoff here, so every direct .complete() call
        # site (writers, critics, implementer) inherits it — a 529 killed
        # an entire 2-hour bench run before this existed.
        response = None
        for attempt in range(4):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                )
                break
            except (
                anthropic.APIStatusError,
                anthropic.APIConnectionError,
            ) as exc:
                status = getattr(exc, "status_code", None)
                transient = status in (429, 500, 502, 503, 529) or isinstance(
                    exc, anthropic.APIConnectionError
                )
                if not transient or attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        if not text.strip():
            # Diagnostics for the empty-response mystery (context voter,
            # PR #9): keep the API's own explanation for the failure note.
            global LAST_EMPTY_META
            LAST_EMPTY_META = {
                "model": model,
                "stop_reason": getattr(response, "stop_reason", None),
                "output_tokens": getattr(
                    getattr(response, "usage", None), "output_tokens", None
                ),
                "content_blocks": [getattr(b, "type", "?") for b in response.content],
            }
        return text


LAST_EMPTY_META: dict | None = None
