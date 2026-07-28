from pet_harness.memory.base_memory_store import MemoryStoreStatus
from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.memory_models import MemoryItem, RetrievalRequest


class _Index:
    def search(self, dense, sparse, top_k):
        return [MemoryItem("m1", "miku", "default", "fruit", "semantic", "使用者喜歡蘋果", "active", "e1", "2026-01-01T00:00:00+00:00")]


class _Sparse:
    def status(self): return MemoryStoreStatus("ready")
    def encode(self, text): return {1: 1.0}


class _DegradedSparse:
    def status(self): return MemoryStoreStatus("degraded", "missing")
    def encode(self, text): raise AssertionError("degraded sparse must not encode")


def test_retrieve_is_the_fail_open_public_seam():
    result = ContextualMemoryRetriever(_Index(), lambda _: [0.0], _Sparse()).retrieve(RetrievalRequest("miku", "那個呢", "我喜歡什麼水果"))

    assert [item.memory_id for item in result.evidence] == ["m1"]
    assert result.trace.follow_up_reason == "pronoun"


def test_retrieve_falls_back_to_dense_only_when_sparse_is_degraded():
    result = ContextualMemoryRetriever(_Index(), lambda _: [0.0], _DegradedSparse()).retrieve(RetrievalRequest("miku", "查詢"))
    assert [item.memory_id for item in result.evidence] == ["m1"]
    assert result.trace.sparse_available is False


def test_follow_up_uses_previous_turn_when_no_rewriter_is_configured():
    result = ContextualMemoryRetriever(_Index(), lambda _: [0.0], _Sparse()).retrieve(
        RetrievalRequest("miku", "那是幾點？", "我下週三下午三點要看牙醫。", "知道了")
    )
    assert result.trace.rewrite_tier == 1
    assert "我下週三下午三點要看牙醫。" in result.trace.standalone_query
