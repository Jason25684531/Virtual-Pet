from __future__ import annotations

from pet_harness.memory.memory_extractor import LlmMemoryExtractor
from pet_harness.memory.memory_item_repository import MemoryItemRepository
from pet_harness.memory.memory_models import MemoryCandidate
from pet_harness.storage.sqlite_store import SQLiteStore


def test_sheet_c3_sensitive_information_is_not_recorded():
    extractor = LlmMemoryExtractor(
        lambda *_: '[{"memory_key":"使用者.事件.電話","memory_type":"semantic","text":"電話 0900-123-456，地址台北市中正區，密碼 secret123"}]'
    )
    candidates = extractor.extract("c3", "我的電話是 0900-123-456，地址台北市中正區，密碼 secret123", "知道了")
    assert not any(token in candidate.text for candidate in candidates for token in ("0900-123-456", "中正區", "secret123"))
    assert LlmMemoryExtractor(lambda *_: '[{"memory_key":"使用者.喜好.拉麵","memory_type":"semantic","text":"我喜歡拉麵"}]').extract("c3-safe", "我喜歡拉麵", "知道了")


def _repository(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite")
    store.initialize()
    return store, MemoryItemRepository(store, "acceptance")


def test_sheet_c4_forget_instruction_removes_memory_immediately(tmp_path):
    store, repository = _repository(tmp_path)
    repository.upsert_candidates([MemoryCandidate("使用者.喜好.拉麵", "semantic", "我喜歡拉麵", "c4")])
    repository.forget("使用者.喜好.拉麵")
    assert not repository.list_all_active()


def test_sheet_c5_forget_instruction_persists_after_repository_rebuild(tmp_path):
    store, repository = _repository(tmp_path)
    repository.upsert_candidates([MemoryCandidate("使用者.事件.秘密", "semantic", "這是一個秘密", "c5")])
    repository.forget("使用者.事件.秘密")
    rebuilt_store = SQLiteStore(tmp_path / "state.sqlite")
    rebuilt_store.initialize()
    assert not MemoryItemRepository(rebuilt_store, "acceptance").list_all_active()
