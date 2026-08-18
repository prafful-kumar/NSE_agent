from __future__ import annotations

"""Unwired EmbeddingProvider stub.

Phase 4A's research memory is deliberately relational-first (see
db/models.py "Phase 4A" section header). No provider is implemented and no
embedding SDK is added to pyproject.toml — this interface exists only so a
future semantic-retrieval layer (OpenAI -> Voyage -> Local, in that
preference order) can be plugged in later without a schema or call-site
change. Nothing in the codebase imports or instantiates a concrete
EmbeddingProvider yet.
"""

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Protocol for turning text into a fixed-length vector embedding."""

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """Embed a single string."""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple strings in one call."""
