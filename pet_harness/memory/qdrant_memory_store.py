from __future__ import annotations

import threading
import time
from typing import Any

from pet_harness.memory.base_memory_store import BaseMemoryStore, MemoryHit, MemoryStoreStatus

# ONNX/FastEmbed 初始化與推論在 Windows 上不可併發執行(同一限制見
# pet_harness/skills/semantic_skill_retriever.py 的 _INDEX_LOCK);跨所有
# QdrantMemoryStore instance 共用同一把鎖,序列化 init/add/query。
_ONNX_LOCK = threading.Lock()
_IMPORT_RETRY_ATTEMPTS = 3
_IMPORT_RETRY_DELAY_SECONDS = 0.5


class QdrantMemoryStore(BaseMemoryStore):
    """Local-first per-character 對話記憶。初始化、寫入皆在背景執行緒完成,
    絕不阻塞互動路徑;未就緒或發生例外時 recall() fail-open 回傳空清單。"""

    def __init__(
        self,
        *,
        character_id: str,
        collection: str,
        path: str,
        model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        mode: str = "local",
        url: str = "",
        max_items: int = 500,
    ) -> None:
        self._character_id = character_id
        self._collection = collection
        self._path = path
        self._model = model
        self._mode = mode
        self._url = url
        self._max_items = max_items
        self._lock = threading.Lock()
        self._client: Any = None
        self._count = 0
        self._next_id = 0
        self._qdrant_client_class: Any = None
        # ponytail: import 必須在呼叫端(主/UI 執行緒)同步完成,只有 client 建構
        # 與 embedding 才能丟背景執行緒——與 semantic_skill_retriever.py 的
        # QdrantFastEmbedRetriever.index() 同一限制:Windows 不可在 worker
        # 執行緒內第一次 import onnxruntime,否則原生 DLL 初始化可能直接 crash。
        # 重試數次:onnxruntime 原生 DLL 在系統高負載下(例如防毒即時掃描鎖檔)
        # 偶發性初始化失敗,屬瞬時性問題,重試即可恢復。
        last_error: ImportError | None = None
        for attempt in range(_IMPORT_RETRY_ATTEMPTS):
            try:
                import onnxruntime  # noqa: F401
                from qdrant_client import QdrantClient

                self._qdrant_client_class = QdrantClient
                last_error = None
                break
            except ImportError as exc:
                last_error = exc
                if attempt < _IMPORT_RETRY_ATTEMPTS - 1:
                    time.sleep(_IMPORT_RETRY_DELAY_SECONDS)
        if last_error is not None:
            self._status = MemoryStoreStatus("disabled", "qdrant_or_fastembed_not_installed")
            return
        self._status = MemoryStoreStatus("loading")
        threading.Thread(target=self._init_worker, daemon=True, name=f"memory-init-{character_id}").start()

    def status(self) -> MemoryStoreStatus:
        with self._lock:
            return self._status

    def save_turn(self, event_id: str, user_text: str, reply: str) -> None:
        # ponytail: fire-and-forget 背景寫入,不等待 embedding/落盤結果;
        # 互動延遲不因記憶寫入增加一毫秒。
        threading.Thread(
            target=self._save_worker, args=(event_id, user_text, reply), daemon=True, name="memory-write"
        ).start()

    def clear(self) -> None:
        """清空這個角色的長期記憶;人設變更時呼叫,避免舊身份的問答殘留。
        Fire-and-forget 背景執行,recall/save_turn 皆已 fail-open,不需等待完成。"""
        threading.Thread(target=self._clear_worker, daemon=True, name="memory-clear").start()

    def _clear_worker(self) -> None:
        with self._lock:
            client, status = self._client, self._status
        if client is None or status.state != "ready":
            return
        try:
            with _ONNX_LOCK:
                client.delete_collection(collection_name=self._collection)
                client.create_collection(
                    collection_name=self._collection,
                    vectors_config=client.get_fastembed_vector_params(),
                )
            with self._lock:
                self._count = 0
                self._next_id = 0
        except Exception as exc:
            with self._lock:
                self._status = MemoryStoreStatus("degraded", type(exc).__name__)

    def recall(self, query: str, top_k: int = 3) -> list[MemoryHit]:
        with self._lock:
            client, status = self._client, self._status
        if status.state != "ready" or client is None or not query.strip():
            return []
        try:
            with _ONNX_LOCK:
                results = client.query(collection_name=self._collection, query_text=query, limit=top_k)
            return [
                MemoryHit(str(item.metadata.get("event_id", "")), str(item.metadata.get("text", "")), float(item.score))
                for item in results
            ]
        except Exception as exc:  # recall 絕不可讓互動中斷
            with self._lock:
                self._status = MemoryStoreStatus("degraded", type(exc).__name__)
            return []

    def _init_worker(self) -> None:
        try:
            with _ONNX_LOCK:
                client = (
                    self._qdrant_client_class(":memory:") if self._mode == "memory"
                    else self._qdrant_client_class(path=self._path) if self._mode == "local"
                    else self._qdrant_client_class(url=self._url)
                )
                client.set_model(self._model)
                existing = {item.name for item in client.get_collections().collections}
                if self._collection not in existing:
                    client.create_collection(
                        collection_name=self._collection,
                        vectors_config=client.get_fastembed_vector_params(),
                    )
                next_id = self._scan_next_id(client)
                count = client.count(collection_name=self._collection).count
        except Exception as exc:
            with self._lock:
                self._status = MemoryStoreStatus("degraded", type(exc).__name__)
            return
        with self._lock:
            self._client = client
            self._next_id = next_id
            self._count = count
            self._status = MemoryStoreStatus("ready")

    def _scan_next_id(self, client: Any) -> int:
        try:
            points, _ = client.scroll(
                collection_name=self._collection,
                limit=self._max_items + 1,
                with_payload=False,
                with_vectors=False,
            )
            ids = [point.id for point in points]
            return (max(ids) + 1) if ids else 0
        except Exception:
            return 0

    def _save_worker(self, event_id: str, user_text: str, reply: str) -> None:
        with self._lock:
            client, status = self._client, self._status
        if client is None or status.state != "ready":
            return
        document = f"{user_text}\n{reply}"[:2000]
        try:
            with self._lock:
                point_id = self._next_id
                self._next_id += 1
            with _ONNX_LOCK:
                client.add(
                    collection_name=self._collection,
                    documents=[document],
                    metadata=[{"event_id": event_id, "text": document}],
                    ids=[point_id],
                )
            with self._lock:
                self._count += 1
                self._enforce_limit_locked(client)
        except Exception as exc:
            with self._lock:
                self._status = MemoryStoreStatus("degraded", type(exc).__name__)

    def _enforce_limit_locked(self, client: Any) -> None:
        """呼叫端已持有 self._lock。以 scroll() 近似 FIFO 淘汰最舊的點位;
        # ponytail: scroll 回傳順序非嚴格保證時間序,對 500 筆軟上限的近似已足夠,
        # 若需精確順序可改為 payload 存 created_at 後 order_by 查詢。"""
        overflow = self._count - self._max_items
        if overflow <= 0:
            return
        points, _ = client.scroll(
            collection_name=self._collection, limit=overflow, with_payload=False, with_vectors=False
        )
        ids = [point.id for point in points]
        if ids:
            client.delete(collection_name=self._collection, points_selector=ids)
            self._count -= len(ids)
