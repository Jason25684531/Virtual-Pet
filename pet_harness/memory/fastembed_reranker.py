from __future__ import annotations

import threading

from pet_harness.memory.memory_models import RetrievalCandidate
from pet_harness.memory.reranker import Reranker


class FastembedReranker(Reranker):
    """Cross-encoder relevance gate with lazy model loading."""

    def __init__(self, model: str | None = None, threshold: float | None = None) -> None:
        import config

        self._model = model or config.MEMORY_RERANK_MODEL
        self._threshold = config.MEMORY_RERANK_MIN_SCORE if threshold is None else threshold
        self._encoder = None
        self._load_lock = threading.Lock()

    def warmup(self) -> None:
        self._load()

    def _load(self):
        # 建構即載入 ONNX(約 1.4 秒);鎖讓預熱與首輪查詢併發時只載入一次,後到的等載入完成。
        # ponytail: 每個 engine 各一份(切角色多載一次,舊 engine 退役即釋放),
        # 若要跨 engine 共用需處理測試的 sys.modules 注入,見 shared_encoders 的作法。
        with self._load_lock:
            if self._encoder is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                self._encoder = TextCrossEncoder(self._model)
            return self._encoder

    def rerank(self, query: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        encoder = self._load()
        scored = [
            RetrievalCandidate(candidate.item, float(score), candidate.fusion)
            for candidate, score in zip(candidates, encoder.rerank(query, [candidate.item.text for candidate in candidates]))
        ]
        self._last_scores = {candidate.item.memory_id: candidate.score for candidate in scored}
        return sorted((candidate for candidate in scored if candidate.score >= self._threshold), key=lambda candidate: candidate.score, reverse=True)
