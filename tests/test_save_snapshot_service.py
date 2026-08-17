import sqlite3
from pathlib import Path

import pytest

from pet_harness.storage.save_snapshot_service import SaveSnapshotService
from scripts.restore_save import restore


def _source(root: Path) -> None:
    character = root / "data/characters/miku"
    character.mkdir(parents=True)
    with sqlite3.connect(character / "state.db") as conn:
        conn.execute("create table value (item text)")
        conn.execute("insert into value values ('saved')")
    (character / "personal.json").write_text("{}")
    (character / "qdrant").mkdir()
    (character / "qdrant" / "ignored").write_text("no")
    for relative in ("assets/characters/miku/manifest.json", "assets/webm/characters/miku/manifest.json"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")


def test_snapshot_is_atomic_and_restorable(tmp_path):
    _source(tmp_path)
    service = SaveSnapshotService(tmp_path)
    first = service.create_snapshot("miku")
    second = service.create_snapshot("miku")
    assert first != second and (first / "state.db").is_file()
    assert not (first / "qdrant").exists()
    with sqlite3.connect(tmp_path / "data/characters/miku/state.db") as conn:
        conn.execute("delete from value")
    restore("miku", first.name, tmp_path)
    with sqlite3.connect(tmp_path / "data/characters/miku/state.db") as conn:
        assert conn.execute("select item from value").fetchone()[0] == "saved"


def test_restore_refuses_qdrant_lock(tmp_path):
    _source(tmp_path)
    SaveSnapshotService(tmp_path).create_snapshot("miku")
    (tmp_path / "data/characters/miku/qdrant/.lock").write_text("locked")
    with pytest.raises(RuntimeError, match="locked"):
        restore("miku", project_root=tmp_path)
