from __future__ import annotations

from pet_harness.memory.memory_models import RetrievalCandidate


class FastembedReranker:
    """Cross-encoder relevance gate with lazy model loading."""

    def __init__(self, model: str | None = None, threshold: float | None = None) -> None:
        import config

        self._model = model or config.MEMORY_RERANK_MODEL
        self._threshold = config.MEMORY_RERANK_MIN_SCORE if threshold is None else threshold
        self._encoder = None

    def rerank(self, query: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        if self._encoder is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            self._encoder = TextCrossEncoder(self._model, lazy_load=True)
        scored = [
            RetrievalCandidate(candidate.item, float(score), candidate.fusion)
            for candidate, score in zip(candidates, self._encoder.rerank(query, [candidate.item.text for candidate in candidates]))
        ]
        self._last_scores = {candidate.item.memory_id: candidate.score for candidate in scored}
        return sorted((candidate for candidate in scored if candidate.score >= self._threshold), key=lambda candidate: candidate.score, reverse=True)
