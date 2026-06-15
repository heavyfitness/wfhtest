"""Minimal completion interface so LLM backends are swappable."""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMBackend(ABC):
    """A single-shot system+user → text completion provider."""

    name: str = "base"

    @abstractmethod
    def complete(self, *, system: str, prompt: str, max_tokens: int = 8192) -> str:
        """Return the model's text response for one system+user exchange."""
