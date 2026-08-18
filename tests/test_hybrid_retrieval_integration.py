from qdrant_client import QdrantClient
import pytest

from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_models import MemoryItem, RetrievalRequest
from pet_harness.memory.sparse_encoder import JiebaBm25SparseEncoder


class _Dense:
    def embed(self, texts):
        for _ in texts:
            yield [1.0] + [0.0] * 383


class _CountingClient:
    def __init__(self, client):
        self.client = client
        self.query_calls = 0

    def __getattr__(self, name):
        return getattr(self.client, name)

    def query_points(self, **kwargs):
        self.query_calls += 1
        return self.client.query_points(**kwargs)


def test_embedded_qdrant_hybrid_retrieval_end_to_end(tmp_path):
    client = _CountingClient(QdrantClient(path=str(tmp_path)))
    sparse = JiebaBm25SparseEncoder()
    if sparse.status().state != "ready":
        pytest.skip(f"local BM25 encoder unavailable: {sparse.status().reason}")
    store = HybridQdrantMemoryStore(
        character_id="miku",
        path=tmp_path,
        client=client,
        dense_encoder=_Dense(),
        sparse_encoder=sparse,
    )
    item = MemoryItem("00000000-0000-0000-0000-000000000001", "miku", "default", "使用者.喜好.拉麵", "semantic", "我喜歡拉麵", "active", "e1", "2026-01-01T00:00:00+00:00")
    store.index([item])

    result = ContextualMemoryRetriever(store, store.embed_dense, store.sparse_encoder).retrieve(
        RetrievalRequest("miku", "拉麵")
    )

    assert [found.memory_id for found in result.evidence] == [item.memory_id]
    assert result.trace.top_score_kind == "rrf"
    assert client.query_calls == 1
