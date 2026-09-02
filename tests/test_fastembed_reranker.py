from types import SimpleNamespace
import sys

from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.fastembed_reranker import FastembedReranker
from pet_harness.memory.memory_models import MemoryItem, RetrievalCandidate, RetrievalRequest


def _candidate(memory_id="m1"):
    item = MemoryItem(memory_id, "miku", "default", "fruit", "semantic", "apple", "active", "e1", "2026-01-01T00:00:00+00:00")
    return RetrievalCandidate(item, 0.9, "rrf")


def test_reranker_filters_low_scores_and_is_lazy(monkeypatch):
    class Encoder:
        def __init__(self, *args, **kwargs): pass
        def rerank(self, query, documents): return iter([0.2, 0.8])
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", SimpleNamespace(TextCrossEncoder=Encoder))
    reranker = FastembedReranker(threshold=0.5)
    assert reranker._encoder is None
    assert [item.item.memory_id for item in reranker.rerank("fruit", [_candidate("low"), _candidate("high")])] == ["high"]


def test_retriever_records_rerank_trace_and_disabled_state():
    class Index:
        def search(self, *args): return [_candidate()]
    class Reranker:
        def rerank(self, query, candidates): return [RetrievalCandidate(candidates[0].item, 0.8, "rerank")]
    enabled = ContextualMemoryRetriever(Index(), lambda _: [0.0], reranker=Reranker()).retrieve(RetrievalRequest("miku", "fruit"))
    disabled = ContextualMemoryRetriever(Index(), lambda _: [0.0]).retrieve(RetrievalRequest("miku", "fruit"))
    assert enabled.trace.rerank_scores == {"m1": 0.8}
    assert disabled.trace.rerank_status == "not_available"
