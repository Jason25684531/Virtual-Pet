from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import config
from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.memory.fastembed_reranker import FastembedReranker
from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.memory_models import MemoryItem, RetrievalCandidate, RetrievalRequest
from pet_harness.models.events import UserEvent


def _candidate(memory_id: str) -> RetrievalCandidate:
    item = MemoryItem(
        memory_id,
        "shared",
        "default",
        memory_id,
        "domain",
        memory_id,
        "active",
        None,
        "2026-01-01T00:00:00+00:00",
    )
    return RetrievalCandidate(item, 0.9, "rrf")


def test_knowledge_reranker_drops_candidates_below_knowledge_threshold(monkeypatch):
    class Encoder:
        def __init__(self, *args, **kwargs):
            pass

        def rerank(self, query, documents):
            return iter([0.2, 0.8])

    monkeypatch.setitem(
        sys.modules,
        "fastembed.rerank.cross_encoder",
        SimpleNamespace(TextCrossEncoder=Encoder),
    )

    reranker = FastembedReranker(threshold=0.5)
    result = reranker.rerank("query", [_candidate("low"), _candidate("high")])

    assert [candidate.item.memory_id for candidate in result] == ["high"]


def test_shared_index_injects_knowledge_thresholds_without_memory_threshold(monkeypatch):
    from pet_harness.knowledge import shared_index

    captured = {}

    class FakeStore:
        def __init__(self, **kwargs):
            captured["store"] = kwargs
            self.embed_dense = lambda text: [1.0]
            self.sparse_encoder = object()

    class FakeRetriever:
        def __init__(self, store, dense, sparse, reranker=None):
            captured["retriever"] = reranker

    class FakeReranker:
        def __init__(self, **kwargs):
            captured["reranker"] = kwargs

    fake_modules = {
        "pet_harness.memory.hybrid_qdrant_memory_store": SimpleNamespace(
            HybridQdrantMemoryStore=FakeStore
        ),
        "pet_harness.memory.contextual_memory_retriever": SimpleNamespace(
            ContextualMemoryRetriever=FakeRetriever
        ),
        "pet_harness.memory.fastembed_reranker": SimpleNamespace(
            FastembedReranker=FakeReranker
        ),
        "pet_harness.memory.shared_encoders": SimpleNamespace(
            get_dense_encoder=lambda: "dense",
            get_sparse_encoder=lambda: "sparse",
        ),
    }
    for name, module in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setattr(config, "KNOWLEDGE_RAG_ENABLED", True)
    monkeypatch.setattr(config, "KNOWLEDGE_DENSE_MIN_SCORE", 0.31)
    monkeypatch.setattr(config, "KNOWLEDGE_RERANK_MIN_SCORE", 0.72)
    monkeypatch.setattr(config, "KNOWLEDGE_RERANK_ENABLED", True)
    shared_index.get_knowledge_retriever.cache_clear()

    try:
        shared_index.get_knowledge_retriever()
    finally:
        shared_index.get_knowledge_retriever.cache_clear()

    assert captured["store"]["dense_min_score"] == 0.31
    assert captured["reranker"] == {"threshold": 0.72}
    assert config.MEMORY_RERANK_MIN_SCORE != config.KNOWLEDGE_RERANK_MIN_SCORE


def test_knowledge_thresholds_support_environment_overrides(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DENSE_MIN_SCORE", "0.81")
    monkeypatch.setenv("KNOWLEDGE_RERANK_MIN_SCORE", "0.91")
    importlib.reload(config)
    try:
        assert config.KNOWLEDGE_DENSE_MIN_SCORE == 0.81
        assert config.KNOWLEDGE_RERANK_MIN_SCORE == 0.91
    finally:
        monkeypatch.delenv("KNOWLEDGE_DENSE_MIN_SCORE", raising=False)
        monkeypatch.delenv("KNOWLEDGE_RERANK_MIN_SCORE", raising=False)
        importlib.reload(config)


def test_retrieval_trace_reports_the_index_threshold():
    class Index:
        _dense_min_score = 0.31

        def search(self, dense, sparse, top_k):
            return []

    result = ContextualMemoryRetriever(Index(), lambda text: [0.0]).retrieve(
        RetrievalRequest("shared", "game question")
    )

    assert result.trace.dense_min_score == 0.31
    assert result.trace.relevance_gate_enabled is True


def test_prompt_keeps_knowledge_source_and_identity_boundaries(tmp_path):
    (tmp_path / "response_rules.md").write_text(
        "Global source rule.\nKnowledge must be honest.", encoding="utf-8"
    )
    prompt = PromptBuilder(tmp_path).build(
        UserEvent(text="這個數值是多少？"),
        [],
        {},
        persona="我是測試角色，請用我的語氣回答。",
    ).prompt

    assert "Global Response Rules below override persona instructions" in prompt
    assert "If this section is none or does not answer the question" in prompt
    assert prompt.index("## Retrieval Evidence") < prompt.index("## Knowledge Reference")
    assert prompt.index("## Knowledge Reference") < prompt.index("## User Text")
    assert "Global source rule." in prompt

    # The general "say you're not sure" instruction must be the last content the
    # model reads before the Output Contract — closer to generation than the
    # same rule buried mid-document in Global Response Rules.
    assert "never state it with confidence" in prompt
    assert prompt.index("never state it with confidence") < prompt.index("## Output Contract")
    assert prompt.index("Global source rule.") < prompt.index("never state it with confidence")
