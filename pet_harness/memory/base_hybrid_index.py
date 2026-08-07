from __future__ import annotations

from abc import ABC, abstractmethod

from pet_harness.memory.memory_models import MemoryItem


class BaseHybridIndex(ABC):
    """Optional search-index capability implemented by HybridQdrantMemoryStore."""

    @abstractmethod
    def index(self, items: list[MemoryItem]) -> list[str]:
        """Index memory items and return their memory IDs."""

    @abstractmethod
    def search(
        self,
        dense: list[float],
        sparse: dict[int, float] | None,
        top_k: int,
    ) -> list[MemoryItem]:
        """Return dense/sparse hybrid search results."""
