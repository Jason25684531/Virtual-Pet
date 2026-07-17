"""SQLiteStore XP transaction 回歸測試:read-after-write 一致性、missing-row
seed、rollback、連續累加、既有資料相容性、connection lifecycle。全部使用
tmp_path 建立真實 SQLite 檔案,不需 LLM/GUI/網路。"""

from __future__ import annotations

import sqlite3

import pytest

from pet_harness.storage.sqlite_store import DEFAULT_USER_ID, SQLiteStore


def _store(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    return store


class TestUserXpReadAfterWrite:
    def test_returns_updated_value_and_persists(self, tmp_path):
        store = _store(tmp_path)
        store.add_user_xp(10)

        progress = store.add_user_xp(5)

        assert progress["xp_total"] == 15
        assert store.get_user_progress()["xp_total"] == 15


class TestSkillXpReadAfterWrite:
    def test_returns_updated_value_and_persists(self, tmp_path):
        store = _store(tmp_path)
        store.add_skill_xp("joke_skill", 10)

        progress = store.add_skill_xp("joke_skill", 5)

        assert progress["xp_total"] == 15
        assert store.get_skill_progress("joke_skill")["xp_total"] == 15


class TestFirstInsertLevelCalculation:
    def test_first_skill_xp_insert_computes_level_from_delta(self, tmp_path):
        store = _store(tmp_path)

        progress = store.add_skill_xp("new_skill", 250)

        assert progress["xp_total"] == 250
        assert progress["level"] == 3


class TestMissingUserRowIsSeeded:
    def test_missing_default_row_is_seeded_then_updated(self, tmp_path):
        store = _store(tmp_path)
        with store.connect() as conn:
            conn.execute("DELETE FROM user_progress WHERE user_id = ?", (DEFAULT_USER_ID,))

        progress = store.add_user_xp(5)

        assert progress["xp_total"] == 5
        assert store.get_user_progress()["xp_total"] == 5


class _BoomConnection(sqlite3.Connection):
    """sqlite3.Connection 是不可變的 C extension type,實例層級的方法替換行不通;
    改以自訂 factory 子類別注入失敗,驗證真實的 rollback 行為。"""

    def execute(self, sql, *args, **kwargs):
        if sql.strip().upper().startswith("UPDATE USER_PROGRESS"):
            raise RuntimeError("injected failure")
        return super().execute(sql, *args, **kwargs)


class TestRollbackOnException:
    def test_exception_during_update_rolls_back(self, tmp_path, monkeypatch):
        store = _store(tmp_path)
        store.add_user_xp(10)

        original_connect = sqlite3.connect

        def _connect_with_boom(*args, **kwargs):
            kwargs["factory"] = _BoomConnection
            return original_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", _connect_with_boom)

        with pytest.raises(RuntimeError):
            store.add_user_xp(5)

        monkeypatch.undo()
        assert store.get_user_progress()["xp_total"] == 10


class TestConsecutiveAccumulation:
    def test_three_consecutive_updates_accumulate_and_level_transitions(self, tmp_path):
        store = _store(tmp_path)

        progress = None
        for _ in range(3):
            progress = store.add_user_xp(40)

        assert progress["xp_total"] == 120
        assert progress["level"] == 2


class TestExistingDatabaseCompatibility:
    def test_pre_existing_database_values_remain_readable_and_update_correctly(self, tmp_path):
        store = _store(tmp_path)
        with store.connect() as conn:
            conn.execute("UPDATE user_progress SET xp_total = 42, level = 1 WHERE user_id = ?", (DEFAULT_USER_ID,))

        reopened = SQLiteStore(tmp_path / "state.db")
        progress = reopened.add_user_xp(8)

        assert progress["xp_total"] == 50


class TestConnectionLifecycle:
    def test_connections_are_closed_after_success_and_failure(self, tmp_path, monkeypatch):
        store = _store(tmp_path)
        opened: list[sqlite3.Connection] = []
        original_connect = sqlite3.connect

        def _tracking_connect(*args, **kwargs):
            conn = original_connect(*args, **kwargs)
            opened.append(conn)
            return conn

        monkeypatch.setattr(sqlite3, "connect", _tracking_connect)

        store.add_user_xp(1)

        assert opened
        for conn in opened:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
