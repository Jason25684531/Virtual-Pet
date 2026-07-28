import sys
from types import ModuleType, SimpleNamespace

from pet_harness.memory.base_memory_store import MemoryStoreStatus
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore


class _ReadySparseEncoder:
    def status(self):
        return MemoryStoreStatus("ready")


class _Client:
    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name="miku_memory_hybrid")])


def test_hybrid_store_starts_ready_with_the_installed_fastembed_api(monkeypatch):
    calls = []

    class _Dense:
        def __init__(self, model):
            calls.append(model)

        @classmethod
        def list_supported_models(cls):
            return []

    fake_fastembed = ModuleType("fastembed")
    fake_fastembed.TextEmbedding = _Dense
    monkeypatch.setitem(sys.modules, "fastembed", fake_fastembed)
    store = HybridQdrantMemoryStore(character_id="miku", path="unused", client=_Client(), sparse_encoder=_ReadySparseEncoder())
    assert store.status().state == "ready"
    assert calls == ["sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"]
