from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pet_harness.memory.memory_models import MemoryCandidate, MemoryItem
from pet_harness.storage.sqlite_store import DEFAULT_USER_ID, SQLiteStore


class MemoryItemRepository:
    def __init__(self, store: SQLiteStore, character_id: str) -> None:
        self.store, self.character_id = store, character_id

    def upsert_candidates(self, candidates: list[MemoryCandidate]) -> list[MemoryItem]:
        now = datetime.now(UTC)
        saved = []
        with self.store.connect() as conn, conn:
            for candidate in candidates:
                active = conn.execute("SELECT * FROM memory_items WHERE memory_key=? AND status='active'", (candidate.memory_key,)).fetchone()
                if active and active["text"] == candidate.text:
                    saved.append(self._item(active)); continue
                memory_id = str(uuid4())
                if active:
                    conn.execute("UPDATE memory_items SET status='superseded', superseded_by=? WHERE memory_id=?", (memory_id, active["memory_id"]))
                expires = (now + timedelta(days=90)).isoformat() if candidate.memory_type == "episodic" else None
                conn.execute("INSERT INTO memory_items (memory_id, character_id, user_id, memory_key, memory_type, text, source_event_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (memory_id, self.character_id, DEFAULT_USER_ID, candidate.memory_key, candidate.memory_type, candidate.text, candidate.source_event_id, now.isoformat(), expires))
                saved.append(MemoryItem(memory_id, self.character_id, DEFAULT_USER_ID, candidate.memory_key, candidate.memory_type, candidate.text, "active", candidate.source_event_id, now.isoformat(), expires))
        return saved

    def list_pending_index(self) -> list[MemoryItem]:
        with self.store.connect() as conn: rows = conn.execute("SELECT * FROM memory_items WHERE indexed_at IS NULL AND status='active'").fetchall()
        return [self._item(row) for row in rows]

    def mark_indexed(self, memory_ids: list[str]) -> None:
        if memory_ids:
            with self.store.connect() as conn, conn: conn.executemany("UPDATE memory_items SET indexed_at=? WHERE memory_id=?", [(datetime.now(UTC).isoformat(), item) for item in memory_ids])

    def list_all_active(self) -> list[MemoryItem]:
        with self.store.connect() as conn: rows = conn.execute("SELECT * FROM memory_items WHERE status='active'").fetchall()
        return [self._item(row) for row in rows]

    @staticmethod
    def _item(row) -> MemoryItem:
        return MemoryItem(**dict(row))
