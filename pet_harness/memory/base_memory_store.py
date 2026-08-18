from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryHit:
    event_id: str
    text: str
    score: float
    memory_key: str | None = None


@dataclass(frozen=True)
class MemoryStoreStatus:
    state: str = "disabled"
    reason: str | None = None


class BaseMemoryStore(ABC):
    """Fail-open interface for per-character conversation memory."""

    @abstractmethod
    def save_turn(self, event_id: str, user_text: str, reply: str) -> None: ...

    @abstractmethod
    def recall(self, query: str, top_k: int = 3) -> list[MemoryHit]: ...

    @abstractmethod
    def status(self) -> MemoryStoreStatus: ...

    @abstractmethod
    def clear(self) -> None: ...


class NullMemoryStore(BaseMemoryStore):
    """Fail-open fallback when no memory store is configured."""

    def save_turn(self, event_id: str, user_text: str, reply: str) -> None:
        return None

    def recall(self, query: str, top_k: int = 3) -> list[MemoryHit]:
        return []

    def status(self) -> MemoryStoreStatus:
        return MemoryStoreStatus("disabled", "no_memory_store_configured")

    def clear(self) -> None:
        return None
