import threading

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


def test_concurrent_upserts_of_the_same_fact_do_not_duplicate(tmp_path):
    """Regression: separate turns extract memory on their own background thread
    (harness_engine._index_memory_turn); without serialization, two threads can both
    read "no active row yet" and each insert their own point for the same fact."""
    store = SQLiteStore(tmp_path / "state.db"); store.initialize()
    repo = MemoryItemRepository(store, "miku")
    candidate = MemoryCandidate("fruit", "semantic", "喜歡蘋果", "e1")

    barrier = threading.Barrier(8)

    def run():
        barrier.wait()
        repo.upsert_candidates([candidate])

    threads = [threading.Thread(target=run) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(repo.list_all_active()) == 1
