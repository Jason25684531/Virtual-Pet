"""KNOWLEDGE_RAG_ENABLED=False 時，共用單例必須完全不建庫、不載模型（task 4.6）。"""

from __future__ import annotations

import config
from pet_harness.knowledge import shared_index


def _clear_caches():
    shared_index.get_knowledge_retriever.cache_clear()
    shared_index.get_knowledge_keywords.cache_clear()


def test_disabled_flag_short_circuits_before_building_anything(monkeypatch):
    monkeypatch.setattr(config, "KNOWLEDGE_RAG_ENABLED", False)
    _clear_caches()
    try:
        assert shared_index.get_knowledge_retriever() is None
        assert shared_index.get_knowledge_keywords() == frozenset()
    finally:
        _clear_caches()  # 不留殘留快取汙染其他測試


def test_singleton_is_built_only_once(monkeypatch):
    monkeypatch.setattr(config, "KNOWLEDGE_RAG_ENABLED", False)
    _clear_caches()
    try:
        first = shared_index.get_knowledge_retriever()
        second = shared_index.get_knowledge_retriever()
        assert first is second  # lru_cache:同一顆行程內單例,不重建
    finally:
        _clear_caches()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
