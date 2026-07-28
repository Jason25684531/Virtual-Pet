from __future__ import annotations

from abc import ABC, abstractmethod
import json

from pet_harness.memory.memory_models import MemoryCandidate


class BaseMemoryExtractor(ABC):
    @abstractmethod
    def extract(self, event_id: str, user_text: str, reply: str) -> list[MemoryCandidate]: ...


class WholeTurnMemoryExtractor(BaseMemoryExtractor):
    def extract(self, event_id: str, user_text: str, reply: str) -> list[MemoryCandidate]:
        text = user_text.strip()
        return [MemoryCandidate(text[:80] or event_id, "episodic", text, event_id)] if text else []


class LlmMemoryExtractor(BaseMemoryExtractor):
    """Extract user-grounded memory candidates; malformed provider output falls back per turn."""

    def __init__(self, extract_call, fallback: BaseMemoryExtractor | None = None) -> None:
        self.extract_call = extract_call
        self.fallback = fallback or WholeTurnMemoryExtractor()

    def extract(self, event_id: str, user_text: str, reply: str) -> list[MemoryCandidate]:
        try:
            payload = json.loads(self.extract_call(user_text, reply))
            return [
                MemoryCandidate(str(item["memory_key"]), str(item["memory_type"]), str(item["text"]), event_id)
                for item in payload
                if item.get("memory_type") in {"semantic", "episodic"} and item.get("memory_key") and item.get("text")
            ]
        except Exception:
            return self.fallback.extract(event_id, user_text, reply)
