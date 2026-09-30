"""RAG 前的輕量閘門與知識檢索接線：命中才檢索、無命中零延遲、失敗降級、可關閉。"""

from __future__ import annotations

from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.memory.base_memory_store import MemoryStoreStatus
from pet_harness.memory.memory_models import MemoryItem, RetrievalResult, RetrievalTrace

from tests.test_harness_per_character import harness_env  # noqa: F401
from tests.conftest import FakeProvider

_KEYWORDS = frozenset({"生命力", "Vigor"})


def _knowledge_section(prompt: str) -> str:
    return prompt.split("## Knowledge Reference")[1].split("## User Text")[0]


def _knowledge_item(chunk_id: str, text: str) -> MemoryItem:
    return MemoryItem(f"know-{chunk_id}", "shared", "default", chunk_id, "core_system", text, "active", None, "2026-01-01T00:00:00+00:00")


class _SpyKnowledgeRetriever:
    def __init__(self, evidence):
        self.calls = 0
        self._evidence = evidence

    def retrieve(self, request):
        self.calls += 1
        return RetrievalResult(self._evidence, RetrievalTrace.empty(request.current_turn_text))


class _RaisingKnowledgeRetriever:
    def retrieve(self, request):
        raise RuntimeError("qdrant unavailable")


def _make_engine(harness_env, **kwargs):
    tmp_path, agentic_root = harness_env
    return PetHarnessEngine(
        FakeProvider(),
        agentic_root=agentic_root,
        db_path=tmp_path / "state.db",
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json",
        character_id="Choppr",
        **kwargs,
    )


def test_gate_skips_retrieval_when_no_keyword_mentioned(harness_env):
    spy = _SpyKnowledgeRetriever([_knowledge_item("er_core_0001", "生命力決定最大 HP")])
    engine = _make_engine(harness_env, knowledge_retriever=spy, knowledge_keywords=_KEYWORDS)

    engine.handle_event({"text": "今天天氣真好", "source": "test"})

    assert spy.calls == 0
    assert _knowledge_section(engine.last_prompt).strip().endswith("none")


def test_gate_triggers_retrieval_and_injects_evidence_when_keyword_mentioned(harness_env):
    spy = _SpyKnowledgeRetriever([_knowledge_item("er_core_0001", "生命力決定最大 HP")])
    engine = _make_engine(harness_env, knowledge_retriever=spy, knowledge_keywords=_KEYWORDS)

    engine.handle_event({"text": "生命力要點到多少", "source": "test"})

    assert spy.calls == 1
    assert "生命力決定最大 HP" in engine.last_prompt


def test_only_top_context_k_chunks_reach_the_prompt(harness_env, monkeypatch):
    import config
    monkeypatch.setattr(config, "KNOWLEDGE_CONTEXT_K", 1)
    evidence = [_knowledge_item("er_core_0001", "第一則內容"), _knowledge_item("er_core_0002", "第二則內容")]
    spy = _SpyKnowledgeRetriever(evidence)
    engine = _make_engine(harness_env, knowledge_retriever=spy, knowledge_keywords=_KEYWORDS)

    engine.handle_event({"text": "生命力要點到多少", "source": "test"})

    assert "第一則內容" in engine.last_prompt
    assert "第二則內容" not in engine.last_prompt


def test_knowledge_failure_degrades_without_breaking_the_turn(harness_env):
    engine = _make_engine(harness_env, knowledge_retriever=_RaisingKnowledgeRetriever(), knowledge_keywords=_KEYWORDS)

    result = engine.handle_event({"text": "生命力要點到多少", "source": "test"})

    assert result is not None
    assert _knowledge_section(engine.last_prompt).strip().endswith("none")


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
