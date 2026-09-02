from types import SimpleNamespace

from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_models import MemoryItem


class _Client:
    def __init__(self):
        self.created = self.upserted = self.query = None
        self.query_calls = 0

    def get_collections(self):
        return SimpleNamespace(collections=[])

    def create_collection(self, **kwargs):
        self.created = kwargs

    def upsert(self, **kwargs):
        self.upserted = kwargs

    def query_points(self, **kwargs):
        self.query_calls += 1
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


def test_index_does_not_delete_when_collection_exceeds_500_items():
    client = _CappedClient()
    store = HybridQdrantMemoryStore(character_id="miku", path=":memory:", client=client, dense_encoder=_Dense(), sparse_encoder=_Sparse())
    item = MemoryItem("m1", "miku", "default", "fruit", "semantic", "apple", "active", "e1", "2026-01-01T00:00:00+00:00")
    store.index([item])
    assert not hasattr(client, "deleted")


def test_hybrid_search_uses_one_qdrant_query():
    client = _Client()
    store = HybridQdrantMemoryStore(character_id="miku", path=":memory:", client=client, dense_encoder=_Dense(), sparse_encoder=_Sparse())
    store.search([0.0] * 384, {1: 0.5}, 5)
    assert client.query_calls == 1


def test_dense_gate_is_only_applied_to_dense_branch(monkeypatch):
    monkeypatch.setattr("config.MEMORY_DENSE_MIN_SCORE", 0.7)
    client = _Client()
    store = HybridQdrantMemoryStore(character_id="miku", path=":memory:", client=client, dense_encoder=_Dense(), sparse_encoder=_Sparse())
    store.search([0.0] * 384, {1: 0.5}, 5)
    prefetch = client.query["prefetch"]
    assert prefetch[0].score_threshold == 0.7
    assert not hasattr(prefetch[1], "score_threshold") or prefetch[1].score_threshold is None


def test_dense_only_gate_uses_one_query_and_threshold(monkeypatch):
    monkeypatch.setattr("config.MEMORY_DENSE_MIN_SCORE", 0.7)
    client = _Client()
    store = HybridQdrantMemoryStore(character_id="miku", path=":memory:", client=client, dense_encoder=_Dense(), sparse_encoder=_Sparse())
    store.search([0.0] * 384, None, 5)
    assert client.query_calls == 1
    assert client.query["score_threshold"] == 0.7


def test_delete_removes_vector_points():
    client = _CappedClient()
    store = HybridQdrantMemoryStore(character_id="miku", path=":memory:", client=client, dense_encoder=_Dense(), sparse_encoder=_Sparse())
    store.delete(["m1"])
    assert client.deleted["points_selector"].points == ["m1"]
