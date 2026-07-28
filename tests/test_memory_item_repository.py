from pet_harness.memory.memory_item_repository import MemoryItemRepository
from pet_harness.memory.memory_models import MemoryCandidate
from pet_harness.storage.sqlite_store import SQLiteStore


def test_repository_supersedes_changed_value_and_keeps_duplicate(tmp_path):
    store = SQLiteStore(tmp_path / "state.db"); store.initialize()
    repo = MemoryItemRepository(store, "miku")
    first = repo.upsert_candidates([MemoryCandidate("fruit", "semantic", "喜歡蘋果", "e1")])[0]
    assert repo.upsert_candidates([MemoryCandidate("fruit", "semantic", "喜歡蘋果", "e2")])[0].memory_id == first.memory_id
    latest = repo.upsert_candidates([MemoryCandidate("fruit", "semantic", "喜歡梨子", "e3")])[0]
    assert [item.text for item in repo.list_all_active()] == ["喜歡梨子"]
    assert [item.memory_id for item in repo.list_pending_index()] == [latest.memory_id]
