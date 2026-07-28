from __future__ import annotations

import time
from typing import Callable

from pet_harness.memory.memory_models import RetrievalRequest, RetrievalResult, RetrievalTrace
from pet_harness.memory.query_rewriter import FollowUpDetector
from pet_harness.memory.result_policy import ResultPolicy


class ContextualMemoryRetriever:
    def __init__(self, index, dense_encoder: Callable[[str], list[float]], sparse_encoder=None, rewriter=None, policy=None) -> None:
        self.index = index
        self.dense_encoder = dense_encoder
        self.sparse_encoder = sparse_encoder
        self.rewriter = rewriter
        self.policy = policy or ResultPolicy()
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
                query = " ".join(part for part in (request.previous_user_text, request.previous_assistant_text, query) if part)
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
            items = self.index.search(dense, sparse, request.top_k)
            latency["fusion"] = (time.perf_counter() - fusion_started) * 1000
            policy_started = time.perf_counter()
            evidence, dropped = self.policy.apply(items, request.top_k)
            latency["policy"] = (time.perf_counter() - policy_started) * 1000
            latency["total"] = (time.perf_counter() - started) * 1000
            counts = getattr(self.index, "last_search_counts", {})
            trace = RetrievalTrace(
                bool(reason), reason, tier, query,
                dense_hit_count=counts.get("dense", len(items)),
                sparse_hit_count=counts.get("sparse", 0),
                fused_count=counts.get("fused", len(items)),
                policy_dropped=dropped, sparse_available=available, latency_ms=latency,
            )
            return RetrievalResult(evidence, trace)
        except Exception:
            latency["total"] = (time.perf_counter() - started) * 1000
            return RetrievalResult([], RetrievalTrace(bool(reason), reason, 3, query, sparse_available=False, latency_ms=latency))
