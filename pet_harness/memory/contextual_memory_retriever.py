from __future__ import annotations

import time
from typing import Callable

from pet_harness.memory.memory_models import RetrievalCandidate, RetrievalRequest, RetrievalResult, RetrievalTrace
from pet_harness.memory.query_rewriter import FollowUpDetector
from pet_harness.memory.result_policy import ResultPolicy
from pet_harness.memory.reranker import Reranker


class ContextualMemoryRetriever:
    def __init__(self, index, dense_encoder: Callable[[str], list[float]], sparse_encoder=None, rewriter=None, policy=None, reranker: Reranker | None = None) -> None:
        self.index = index
        self.dense_encoder = dense_encoder
        self.sparse_encoder = sparse_encoder
        self.rewriter = rewriter
        self.policy = policy or ResultPolicy()
        self.reranker = reranker
        self.detector = FollowUpDetector()

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        started = time.perf_counter()
        reason = self.detector.detect(request)
        query, tier = request.current_turn_text, 2
        latency: dict[str, float] = {}
        if reason:
            rewritten = None
            if self.rewriter:
                rewrite_started = time.perf_counter()
                rewritten = self.rewriter.rewrite(request)
                latency["rewrite"] = (time.perf_counter() - rewrite_started) * 1000
            if rewritten:
                query, tier = rewritten, 0
            else:
                assistant = request.previous_assistant_text if (request.previous_assistant_text or "").rstrip().endswith(("?", "？")) else None
                query = " ".join(part for part in (request.previous_user_text, assistant, query) if part)
                tier = 1
        try:
            dense_started = time.perf_counter()
            dense = self.dense_encoder(query)
            latency["dense"] = (time.perf_counter() - dense_started) * 1000
            available = bool(self.sparse_encoder and self.sparse_encoder.status().state == "ready")
            sparse = None
            if available:
                sparse_started = time.perf_counter()
                sparse = self.sparse_encoder.encode(query)
                latency["sparse"] = (time.perf_counter() - sparse_started) * 1000
            fusion_started = time.perf_counter()
            candidates = self.index.search(dense, sparse, request.top_k)
            latency["fusion"] = (time.perf_counter() - fusion_started) * 1000
            rerank_status = "not_available"
            if self.reranker:
                rerank_started = time.perf_counter()
                candidates = self.reranker.rerank(query, candidates)
                latency["rerank"] = (time.perf_counter() - rerank_started) * 1000
                rerank_status = "available"
            items = [candidate.item if isinstance(candidate, RetrievalCandidate) else candidate for candidate in candidates]
            rerank_ranks = {item.memory_id: rank for rank, item in enumerate(items, 1)} if self.reranker else {}
            rerank_scores = {candidate.item.memory_id: float(candidate.score) for candidate in candidates} if self.reranker else {}
            policy_started = time.perf_counter()
            evidence, dropped = self.policy.apply(items, request.top_k)
            latency["policy"] = (time.perf_counter() - policy_started) * 1000
            latency["total"] = (time.perf_counter() - started) * 1000
            top = candidates[0] if candidates else None
            top_score = float(top.score) if isinstance(top, RetrievalCandidate) else None
            top_kind = top.fusion if isinstance(top, RetrievalCandidate) else ("rrf" if available else "cosine")
            threshold = __import__("config").MEMORY_DENSE_MIN_SCORE
            trace = RetrievalTrace(
                bool(reason), reason, tier, query,
                fused_count=len(candidates), dense_attempted=True, sparse_attempted=available,
                top_score=top_score,
                top_score_kind=top_kind,
                relevance_gate_enabled=threshold > 0, dense_min_score=threshold,
                policy_dropped=dropped, sparse_available=available, latency_ms=latency,
                rerank_status=rerank_status,
                rerank_ranks=rerank_ranks,
                rerank_scores=rerank_scores,
            )
            return RetrievalResult(evidence, trace)
        except Exception:
            latency["total"] = (time.perf_counter() - started) * 1000
            return RetrievalResult([], RetrievalTrace(bool(reason), reason, 3, query, sparse_available=False, latency_ms=latency))

    def warmup(self, character_id: str) -> RetrievalResult:
        """Exercise the real read path without inserting synthetic memory."""
        return self.retrieve(RetrievalRequest(character_id, "記憶預熱"))
