"""reduce-turn-latency 4.3:預熱要載入重排序模型,不能只靠「記憶預熱」查詢碰巧走到 rerank。"""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.fastembed_reranker import FastembedReranker


def _install_fake_encoder(monkeypatch, constructed, delay=0.0):
    class Encoder:
        def __init__(self, *args, **kwargs):
            time.sleep(delay)
            constructed.append(kwargs)

        def rerank(self, query, documents):
            return iter([0.9] * len(documents))

    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", SimpleNamespace(TextCrossEncoder=Encoder))


def test_warmup_loads_encoder_eagerly_without_any_candidates(monkeypatch):
    constructed = []
    _install_fake_encoder(monkeypatch, constructed)
    reranker = FastembedReranker()
    assert reranker._encoder is None
    reranker.warmup()
    assert len(constructed) == 1 and reranker._encoder is not None
    assert "lazy_load" not in constructed[0]  # 建構即載入,不是延後到第一次 rerank


def test_concurrent_warmup_and_rerank_load_once(monkeypatch):
    constructed = []
    _install_fake_encoder(monkeypatch, constructed, delay=0.05)
    reranker = FastembedReranker()
    threads = [threading.Thread(target=reranker.warmup) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(constructed) == 1


def test_retriever_warmup_calls_reranker_even_when_search_finds_nothing():
    calls = []

    class Index:
        def search(self, *a):
            return []  # 「記憶預熱」查不到候選,rerank 不會被呼叫

    class Reranker:
        def warmup(self):
            calls.append("warmup")

        def rerank(self, query, candidates):
            if not candidates:  # 與 FastembedReranker.rerank 相同:空候選提前返回,不載入模型
                return []
            calls.append("rerank")
            return candidates

    ContextualMemoryRetriever(Index(), lambda _: [0.0], reranker=Reranker()).warmup("char-Adol")
    assert calls == ["warmup"]


def test_reranker_warmup_failure_does_not_block_rest_of_warmup():
    searched = []

    class Index:
        def search(self, *a):
            searched.append(1)
            return []

    class Reranker:
        def warmup(self):
            raise RuntimeError("model missing")

    ContextualMemoryRetriever(Index(), lambda _: [0.0], reranker=Reranker()).warmup("char-Adol")
    assert searched == [1]  # 重排序載入失敗,檢索路徑仍照常預熱


def test_retriever_without_reranker_warmup_unchanged():
    class Index:
        def search(self, *a):
            return []

    ContextualMemoryRetriever(Index(), lambda _: [0.0]).warmup("char-Adol")  # reranker=None 不得拋例外
