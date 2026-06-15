"""Local Ollama backend — point LLM_BACKEND=ollama at your own GPU box.

Speaks Ollama's /api/chat with format="json", so it honours the same
JSON-only contract as the Anthropic backend.
"""
from __future__ import annotations

import logging
import time

import httpx

from .base import LLMBackend

logger = logging.getLogger(__name__)


class OllamaBackend(LLMBackend):
    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1",
        *,
        timeout: float = 300.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self.model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    def complete(self, *, system: str, prompt: str, max_tokens: int = 8192) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = httpx.post(
                    f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
                )
                response.raise_for_status()
                return str(response.json()["message"]["content"])
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise RuntimeError(
                        f"Ollama rejected the request ({exc.response.status_code}): "
                        f"{exc.response.text[:200]}"
                    ) from exc
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc
            if attempt < self._max_retries:
                delay = self._backoff_base * 2**attempt
                logger.warning(
                    "Ollama request failed (attempt %d/%d): %s — retrying in %.0fs",
                    attempt + 1,
                    self._max_retries + 1,
                    last_error,
                    delay,
                )
                time.sleep(delay)
        raise RuntimeError(
            f"Ollama request failed after {self._max_retries + 1} attempts"
        ) from last_error
