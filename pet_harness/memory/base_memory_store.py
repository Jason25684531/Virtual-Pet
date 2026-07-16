from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryHit:
    event_id: str
    text: str
    score: float


@dataclass(frozen=True)
class MemoryStoreStatus:
    state: str = "disabled"
    reason: str | None = None


class BaseMemoryStore(ABC):
    """跨輪對話記憶介面;callers 只依賴此 ABC,不得依賴具體實作
    (見 fix-core-interaction-experience / conversation-memory)。"""

    @abstractmethod
    def save_turn(self, event_id: str, user_text: str, reply: str) -> None: ...

    @abstractmethod
    def recall(self, query: str, top_k: int = 3) -> list[MemoryHit]: ...

    @abstractmethod
    def status(self) -> MemoryStoreStatus: ...

    @abstractmethod
    def clear(self) -> None: ...


class NullMemoryStore(BaseMemoryStore):
    """未注入實際記憶庫時的預設值:零執行緒、零磁碟/網路存取,恆為 fail-open 空結果。
    供未走 CharacterRouter(例如單元測試直接建構 PetHarnessEngine)的呼叫端使用,
    真正的 QdrantMemoryStore 由 CharacterRouter.switch_character 依 character_id 注入。"""

    def save_turn(self, event_id: str, user_text: str, reply: str) -> None:
        return None

    def recall(self, query: str, top_k: int = 3) -> list[MemoryHit]:
        return []

    def status(self) -> MemoryStoreStatus:
        return MemoryStoreStatus("disabled", "no_memory_store_configured")

    def clear(self) -> None:
        return None
