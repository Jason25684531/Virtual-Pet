from __future__ import annotations

from typing import Protocol

from pet_harness.memory.memory_models import RetrievalCandidate


class Reranker(Protocol):
    """Orders retrieved candidates by a model-derived relevance score."""

    def rerank(self, query: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]: ...
