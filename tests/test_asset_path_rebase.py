from pathlib import Path

from pet_harness.storage import sqlite_store
from pet_harness.storage.sqlite_store import SQLiteStore


def _app(tmp_path, monkeypatch):
    app = tmp_path / "new_root"
    motion = app / "assets" / "characters" / "char-Adol" / "motions" / "og" / "idle.webm"
    motion.parent.mkdir(parents=True)
    motion.write_bytes(b"webm")
    monkeypatch.setattr(sqlite_store, "APP_ROOT", app)
    return motion


def test_old_root_absolute_path_is_rebased_to_app_root(tmp_path, monkeypatch):
    motion = _app(tmp_path, monkeypatch)
    old = r"D:\old_machine\Virtual-Pet\assets\characters\char-Adol\motions\og\idle.webm"
    assert Path(SQLiteStore._rebase(old)) == motion


def test_existing_path_is_unchanged(tmp_path, monkeypatch):
    motion = _app(tmp_path, monkeypatch)
    assert SQLiteStore._rebase(str(motion)) == str(motion)


def test_path_without_assets_segment_is_unchanged(tmp_path, monkeypatch):
    _app(tmp_path, monkeypatch)
    old = r"D:\old_machine\elsewhere\idle.webm"
    assert SQLiteStore._rebase(old) == old


def test_missing_after_rebase_returns_original(tmp_path, monkeypatch):
    _app(tmp_path, monkeypatch)
    old = r"D:\old_machine\Virtual-Pet\assets\characters\char-Adol\motions\og\gone.webm"
    assert SQLiteStore._rebase(old) == old


def test_listed_assets_and_generation_lookup_use_rebased_path(tmp_path, monkeypatch):
    motion = _app(tmp_path, monkeypatch)
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    old = r"D:\old_machine\Virtual-Pet\assets\characters\char-Adol\motions\og\idle.webm"
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO character_assets (asset_id, character_id, asset_type, variant, motion_key, file_path, filename, mime_type, sha256, version, active, generation_index, created_at)"
            " VALUES ('a1', 'char-Adol', 'motion_webm', 'og', 'idle', ?, 'idle.webm', 'video/webm', 'x', 1, 1, 1, '2026-10-01')",
            (old,),
        )
    rows = store.list_character_assets("char-Adol")
    assert Path(rows[0]["file_path"]) == motion
    assert store.generation_for_file(motion) == 1
