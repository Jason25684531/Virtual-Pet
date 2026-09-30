import sys
from types import ModuleType, SimpleNamespace

import pytest

from pet_harness.memory.base_memory_store import MemoryStoreStatus
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore


@pytest.fixture(autouse=True)
def _fake_qdrant_client_module(monkeypatch):
    # 這台機器上真正的 qdrant_client 匯入偶爾會被 Windows 應用程式控制原則擋下
    # （見 hybrid_qdrant_memory_store.py 的重試邏輯），用假模組隔離測試，不受
    # 環境當下是否觸發封鎖影響。
    fake = ModuleType("qdrant_client")
    fake.QdrantClient = object
    fake.models = SimpleNamespace(
        VectorParams=lambda **kw: kw, Distance=SimpleNamespace(COSINE="cosine"),
        SparseVectorParams=lambda **kw: kw, Modifier=SimpleNamespace(IDF="idf"),
    )
    monkeypatch.setitem(sys.modules, "qdrant_client", fake)


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


class _BrokenClient:
    def get_collections(self):
        raise ImportError("DLL load failed while importing cygrpc")


def test_hybrid_store_logs_when_init_fails(monkeypatch, caplog):
    monkeypatch.setattr("pet_harness.memory.hybrid_qdrant_memory_store.time.sleep", lambda _: None)
    with caplog.at_level("WARNING"):
        store = HybridQdrantMemoryStore(character_id="miku", path="unused", client=_BrokenClient(), sparse_encoder=_ReadySparseEncoder())
    assert store.status().state == "degraded"
    assert caplog.text.count("cygrpc") == 3  # 2 retry warnings + 1 final degraded error
    assert "degraded" in caplog.text


def test_hybrid_store_recovers_after_transient_failure(monkeypatch):
    monkeypatch.setattr("pet_harness.memory.hybrid_qdrant_memory_store.time.sleep", lambda _: None)

    class _FlakyClient:
        calls = 0

        def get_collections(self):
            _FlakyClient.calls += 1
            if _FlakyClient.calls < 2:
                raise ImportError("DLL load failed while importing cygrpc")
            return SimpleNamespace(collections=[SimpleNamespace(name="miku_memory_hybrid")])

    store = HybridQdrantMemoryStore(character_id="miku", path="unused", client=_FlakyClient(), sparse_encoder=_ReadySparseEncoder())
    assert store.status().state == "ready"
