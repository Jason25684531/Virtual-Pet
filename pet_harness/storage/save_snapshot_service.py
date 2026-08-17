from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4


class SaveSnapshotService:
    def __init__(self, project_root: str | Path = ".") -> None:
        self.project_root = Path(project_root).resolve()

    def create_snapshot(self, character_id: str) -> Path:
        source = self.project_root / "data" / "characters" / character_id
        state_db = source / "state.db"
        if not state_db.is_file():
            raise FileNotFoundError(state_db)
        parent = self.project_root / "data" / "saves" / character_id
        parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = parent / stamp
        suffix = 2
        while target.exists():
            target = parent / f"{stamp}-{suffix}"
            suffix += 1
        staging = parent / f".{target.name}-{uuid4().hex}"
        try:
            staging.mkdir()
            source_conn = sqlite3.connect(state_db)
            target_conn = sqlite3.connect(staging / "state.db")
            try:
                source_conn.backup(target_conn)
            finally:
                target_conn.close()
                source_conn.close()
            character = staging / "character"
            character.mkdir()
            for path in source.glob("*.json"):
                shutil.copy2(path, character / path.name)
            manifests = staging / "assets_manifests"
            for relative in (Path("assets/characters") / character_id / "manifest.json", Path("assets/webm/characters") / character_id / "manifest.json"):
                path = self.project_root / relative
                if path.is_file():
                    destination = manifests / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
            staging.rename(target)
            return target
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
