from __future__ import annotations
from datetime import UTC, datetime
from pet_harness.memory.memory_models import MemoryItem

class ResultPolicy:
    def apply(self, items: list[MemoryItem], top_k: int) -> tuple[list[MemoryItem], dict[str, int]]:
        dropped = {"superseded": 0, "expired": 0, "duplicate": 0}; seen = set(); result = []
        now = datetime.now(UTC)
        for item in items:
            if item.status != "active": dropped["superseded"] += 1; continue
            if item.expires_at and datetime.fromisoformat(item.expires_at) <= now: dropped["expired"] += 1; continue
            if item.memory_key in seen: dropped["duplicate"] += 1; continue
            seen.add(item.memory_key); result.append(item)
            if len(result) == top_k: break
        return result, dropped
