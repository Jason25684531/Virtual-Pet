from types import SimpleNamespace

from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_models import MemoryItem


class _Client:
    def __init__(self):
        self.created = self.upserted = self.query = None

    def get_collections(self):
        return SimpleNamespace(collections=[])

    def create_collection(self, **kwargs):
        self.created = kwargs

    def upsert(self, **kwargs):
        self.upserted = kwargs

    def query_points(self, **kwargs):
        self.query = kwargs
        return SimpleNamespace(points=[])


class _CappedClient(_Client):
    def count(self, **_kwargs):
        return SimpleNamespace(count=501)

    def scroll(self, **_kwargs):
        return [SimpleNamespace(id="old")], None

    def delete(self, **kwargs):
        self.deleted = kwargs


class _Dense:
    def embed(self, texts):
        yield [0.0] * 384


class _Sparse:
    def encode(self, _text):
        return {1: 0.5}


def test_hybrid_collection_uses_named_dense_sparse_vectors_and_idf():
    client = _Client()
    HybridQdrantMemoryStore(character_id="miku", path=":memory:", client=client, dense_encoder=_Dense(), sparse_encoder=_Sparse())
    assert set(client.created["vectors_config"]) == {"dense"}
    assert client.created["sparse_vectors_config"]["sparse"].modifier.value == "idf"


def test_index_writes_structured_payload_and_search_uses_rrf():
    client = _Client()
    store = HybridQdrantMemoryStore(character_id="miku", path=":memory:", client=client, dense_encoder=_Dense(), sparse_encoder=_Sparse())
    item = MemoryItem("m1", "miku", "default", "fruit", "semantic", "喜歡蘋果", "active", "e1", "2026-01-01T00:00:00+00:00")
    assert store.index([item]) == ["m1"]
    assert {"memory_key", "memory_type", "status", "expires_at", "source_event_id", "schema_version"} <= set(client.upserted["points"][0].payload)
    assert store.search([0.0] * 384, {1: 0.5}, 5) == []
    assert client.query["query"].fusion.value == "rrf"


def test_index_keeps_qdrant_collection_at_500_items():
    client = _CappedClient()
    store = HybridQdrantMemoryStore(character_id="miku", path=":memory:", client=client, dense_encoder=_Dense(), sparse_encoder=_Sparse())
    item = MemoryItem("m1", "miku", "default", "fruit", "semantic", "apple", "active", "e1", "2026-01-01T00:00:00+00:00")
    store.index([item])
    assert client.deleted["points_selector"] == ["old"]
