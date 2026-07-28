from __future__ import annotations

from pathlib import Path
from typing import Any

from pet_harness.memory.base_hybrid_index import BaseHybridIndex
from pet_harness.memory.base_memory_store import BaseMemoryStore, MemoryHit, MemoryStoreStatus
from pet_harness.memory.memory_models import MemoryItem
from pet_harness.memory.sparse_encoder import JiebaBm25SparseEncoder


class HybridQdrantMemoryStore(BaseMemoryStore, BaseHybridIndex):
    """Per-character hybrid search index; SQLite remains the source of truth."""

    MAX_ITEMS = 500

    def __init__(
        self,
        *,
        character_id: str,
        path: str | Path,
        client: Any | None = None,
        dense_encoder=None,
        sparse_encoder: JiebaBm25SparseEncoder | None = None,
    ) -> None:
        self.character_id = character_id
        self.collection = f"{character_id}_memory_hybrid"
        self._client = client
        self._dense_encoder = dense_encoder
        self.sparse_encoder = sparse_encoder or JiebaBm25SparseEncoder()
        self.last_search_counts = {"dense": 0, "sparse": 0, "fused": 0}
        try:
            self._ensure_ready(path)
            self._status = MemoryStoreStatus("ready")
        except Exception as exc:
            self._status = MemoryStoreStatus("degraded", str(exc) or type(exc).__name__)

    def _ensure_ready(self, path: str | Path) -> None:
        from qdrant_client import QdrantClient, models

        if self._client is None:
            self._client = QdrantClient(path=str(path))
        if self._dense_encoder is None:
            from fastembed import TextEmbedding

            self._dense_encoder = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        names = {item.name for item in self._client.get_collections().collections}
        if self.collection not in names:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config={"dense": models.VectorParams(size=384, distance=models.Distance.COSINE)},
                sparse_vectors_config={"sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )

    def status(self) -> MemoryStoreStatus:
        return self._status

    def save_turn(self, event_id: str, user_text: str, reply: str) -> None:
        return None

    def recall(self, query: str, top_k: int = 3) -> list[MemoryHit]:
        if self._status.state != "ready" or not query.strip():
            return []
        dense = self.embed_dense(query)
        items = self.search(dense, self.sparse_encoder.encode(query), top_k)
        return [MemoryHit(item.source_event_id or "", item.text, 0.0) for item in items]

    def embed_dense(self, text: str) -> list[float]:
        return list(next(self._dense_encoder.embed([text])))

    def index(self, items: list[MemoryItem]) -> list[str]:
        if self._status.state != "ready" or not items:
            return []
        from qdrant_client import models

        points = []
        for item in items:
            sparse = self.sparse_encoder.encode(item.text)
            vectors: dict[str, Any] = {"dense": self.embed_dense(item.text)}
            if sparse:
                vectors["sparse"] = models.SparseVector(indices=list(sparse), values=list(sparse.values()))
            points.append(models.PointStruct(id=item.memory_id, vector=vectors, payload=self._payload(item)))
        self._client.upsert(collection_name=self.collection, points=points, wait=True)
        self._enforce_limit()
        return [item.memory_id for item in items]

    def _enforce_limit(self) -> None:
        count = getattr(self._client, "count", None)
        scroll = getattr(self._client, "scroll", None)
        delete = getattr(self._client, "delete", None)
        if not all(callable(operation) for operation in (count, scroll, delete)):
            return
        overflow = count(collection_name=self.collection).count - self.MAX_ITEMS
        if overflow <= 0:
            return
        points, _ = scroll(collection_name=self.collection, limit=overflow, with_payload=False, with_vectors=False)
        if points:
            delete(collection_name=self.collection, points_selector=[point.id for point in points])

    @staticmethod
    def _payload(item: MemoryItem) -> dict[str, Any]:
        return {
            "memory_id": item.memory_id,
            "character_id": item.character_id,
            "user_id": item.user_id,
            "memory_key": item.memory_key,
            "memory_type": item.memory_type,
            "text": item.text,
            "status": item.status,
            "source_event_id": item.source_event_id,
            "created_at": item.created_at,
            "expires_at": item.expires_at,
            "schema_version": item.schema_version,
        }

    def search(self, dense: list[float], sparse: dict[int, float] | None, top_k: int) -> list[MemoryItem]:
        if self._status.state != "ready":
            return []
        from qdrant_client import models

        active = models.Filter(must=[models.FieldCondition(key="status", match=models.MatchValue(value="active"))])
        if not sparse:
            response = self._client.query_points(
                collection_name=self.collection, query=dense, using="dense", query_filter=active,
                limit=top_k, with_payload=True,
            )
            points = response.points
            self.last_search_counts = {"dense": len(points), "sparse": 0, "fused": len(points)}
            return [self._item(point.payload) for point in points]
        dense_points = self._client.query_points(
            collection_name=self.collection, query=dense, using="dense", query_filter=active, limit=20, with_payload=False,
        ).points
        sparse_vector = models.SparseVector(indices=list(sparse), values=list(sparse.values()))
        sparse_points = self._client.query_points(
            collection_name=self.collection, query=sparse_vector, using="sparse", query_filter=active, limit=20, with_payload=False,
        ).points
        prefetch = [models.Prefetch(query=dense, using="dense", limit=20, filter=active)]
        prefetch.append(models.Prefetch(
                query=sparse_vector,
                using="sparse", limit=20, filter=active,
        ))
        response = self._client.query_points(
            collection_name=self.collection,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
        points = response.points
        self.last_search_counts = {"dense": len(dense_points), "sparse": len(sparse_points), "fused": len(points)}
        return [self._item(point.payload) for point in points]

    @staticmethod
    def _item(payload: dict[str, Any]) -> MemoryItem:
        return MemoryItem(
            memory_id=str(payload["memory_id"]), character_id=str(payload["character_id"]),
            user_id=str(payload.get("user_id", "default")), memory_key=str(payload["memory_key"]),
            memory_type=str(payload["memory_type"]), text=str(payload["text"]), status=str(payload["status"]),
            source_event_id=payload.get("source_event_id"), created_at=str(payload["created_at"]),
            expires_at=payload.get("expires_at"), schema_version=int(payload.get("schema_version", 1)),
        )

    def clear(self) -> None:
        if self._status.state != "ready":
            return
        self._client.delete_collection(self.collection)
        self._ensure_ready(":memory:")

    def shutdown(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
