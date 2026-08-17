from __future__ import annotations

import threading

from pet_harness.memory.base_memory_store import MemoryStoreStatus


class JiebaBm25SparseEncoder:
    _ONNX_LOCK = threading.Lock()

    def __init__(self) -> None:
        try:
            import jieba
            from fastembed.sparse.bm25 import Bm25

            self._cut = jieba.lcut
            with self._ONNX_LOCK:
                self._encoder = Bm25("Qdrant/bm25", disable_stemmer=True)
            self._status = MemoryStoreStatus("ready")
        except Exception as exc:
            self._cut = self._encoder = None
            self._status = MemoryStoreStatus("degraded", type(exc).__name__)

    def encode(self, text: str) -> dict[int, float]:
        if self._status.state != "ready" or not text.strip():
            return {}
        segmented = " ".join(token for token in self._cut(text) if token.strip())
        try:
            with self._ONNX_LOCK:
                embedding = next(self._encoder.embed([segmented]))
            return {int(index): float(value) for index, value in zip(embedding.indices, embedding.values)}
        except Exception as exc:
            self._status = MemoryStoreStatus("degraded", type(exc).__name__)
            return {}

    def status(self) -> MemoryStoreStatus:
        return self._status
