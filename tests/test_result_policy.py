from datetime import UTC, datetime, timedelta

from pet_harness.memory.memory_models import MemoryItem
from pet_harness.memory.result_policy import ResultPolicy


def _item(memory_id, key, *, status="active", expires_at=None):
    return MemoryItem(memory_id, "miku", "default", key, "semantic", key, status, "e1", "2026-01-01T00:00:00+00:00", expires_at)


def test_result_policy_removes_inactive_expired_and_duplicate_items():
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    evidence, dropped = ResultPolicy().apply([
        _item("1", "fruit"), _item("2", "fruit"), _item("3", "old", status="superseded"), _item("4", "past", expires_at=expired),
    ], 5)
    assert [item.memory_id for item in evidence] == ["1"]
    assert dropped == {"superseded": 1, "expired": 1, "duplicate": 1}
