"""Anthropic Messages API backend (the default)."""
from __future__ import annotations

import anthropic

from .base import LLMBackend


class AnthropicBackend(LLMBackend):
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_retries: int = 3,
        timeout: float = 180.0,
    ) -> None:
        # The SDK retries 429/5xx/connection errors with backoff on its own.
        self._client = anthropic.Anthropic(
            api_key=api_key, max_retries=max_retries, timeout=timeout
        )
        self.model = model

    def complete(self, *, system: str, prompt: str, max_tokens: int = 8192) -> str:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        if message.stop_reason == "max_tokens":
            raise RuntimeError(
                "Anthropic response was truncated (stop_reason=max_tokens); "
                "raise max_tokens or shorten the prompt"
            )
        return "".join(block.text for block in message.content if block.type == "text")
