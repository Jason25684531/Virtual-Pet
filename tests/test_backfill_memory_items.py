import sys
from types import SimpleNamespace

from scripts import backfill_memory_items
from pet_harness.memory.memory_item_repository import MemoryItemRepository
from pet_harness.memory.memory_models import MemoryCandidate
from pet_harness.storage.sqlite_store import SQLiteStore


def test_backfill_cli_supersedes_existing_invalid_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = SQLiteStore(tmp_path / "data" / "characters" / "miku" / "state.db")
    store.initialize()
    repository = MemoryItemRepository(store, "miku")
    invalid = repository.upsert_candidates([MemoryCandidate("角色.希望", "episodic", "希望你一切順利", "e1")])[0]
    monkeypatch.setattr(sys, "argv", ["backfill_memory_items.py", "--character", "miku"])
    monkeypatch.setitem(sys.modules, "qdrant_client", SimpleNamespace(QdrantClient=object))

    assert backfill_memory_items.main() == 0

    with store.connect() as conn:
        row = conn.execute("SELECT status, superseded_by FROM memory_items WHERE memory_id=?", (invalid.memory_id,)).fetchone()
    assert dict(row) == {"status": "superseded", "superseded_by": None}
