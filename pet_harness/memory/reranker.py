from __future__ import annotations

from typing import Protocol, runtime_checkable

from pet_harness.memory.memory_models import RetrievalCandidate


@runtime_checkable
class Reranker(Protocol):
    """Orders retrieved candidates by a model-derived relevance score."""

    def rerank(self, query: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]: ...
