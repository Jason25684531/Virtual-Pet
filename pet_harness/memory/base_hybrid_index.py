from __future__ import annotations

from abc import ABC, abstractmethod

from pet_harness.memory.memory_models import MemoryItem


class BaseHybridIndex(ABC):
    """Memory Item 的 Search Index 介面(ADR-0003:Qdrant 是可重建索引,不是資料庫)。

    刻意與 BaseMemoryStore 分開:BaseMemoryStore 描述的是「整輪對話記憶」這個舊角色,
    而 QdrantMemoryStore 的 collection 沒有 sparse vector 也沒有 payload schema,
    結構上不可能索引 Memory Item。把 index()/search() 放進 BaseMemoryStore 會逼
    NullMemoryStore、QdrantMemoryStore 與測試 fake 各寫一份永遠沒有意義的 stub。
    """

    @abstractmethod
    def index(self, items: list[MemoryItem]) -> list[str]:
        """建立索引,回傳成功索引的 memory_id。MUST NOT 拋例外至互動路徑;
        失敗時回傳空清單,由呼叫端保留 indexed_at=NULL 待下次補索引。"""

    @abstractmethod
    def search(
        self,
        dense: list[float],
        sparse: dict[int, float] | None,
        top_k: int,
    ) -> list[MemoryItem]:
        """Dense + Sparse prefetch 後以 RRF 融合。sparse 為 None 時降級為
        Dense-only(ADR-0005 Sparse Failure Fallback)。MUST NOT 拋例外。"""
