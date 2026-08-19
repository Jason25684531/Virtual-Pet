import json
import sqlite3
import subprocess
import sys
import types

import character_library as library_module
from character_library import CharacterLibrary
from pet_harness.asset.asset_models import AssetJob, GeneratedAsset, JobStatus
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.asset.comfyui_client import ComfyUIClient
from pet_harness.storage.sqlite_store import SQLiteStore


def test_character_library_import_does_not_eagerly_load_engine_cycle():
    result = subprocess.run(
        [sys.executable, "-c", "import character_library"],
        capture_output=True,
        text=True,
        cwd=library_module.PROJECT_ROOT,
    )

    assert result.returncode == 0, result.stderr


def test_old_asset_schema_migrates_idempotently_without_changing_rows(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE user_progress (user_id TEXT PRIMARY KEY, xp_total INTEGER, level INTEGER, updated_at TEXT);
        CREATE TABLE behavior_state (state_key TEXT PRIMARY KEY, behavior_id TEXT, updated_at TEXT);
        CREATE TABLE asset_jobs (job_id TEXT PRIMARY KEY, parent_job_id TEXT, character_id TEXT, workflow_type TEXT, variant TEXT,
          motion_key TEXT, status TEXT, comfy_prompt_id TEXT, idempotency_key TEXT UNIQUE, retry_count INTEGER, max_retries INTEGER,
          timeout_sec INTEGER, error_code TEXT, error_message TEXT, metadata_json TEXT, created_at TEXT, started_at TEXT, completed_at TEXT);
        CREATE TABLE character_assets (asset_id TEXT PRIMARY KEY, character_id TEXT, asset_type TEXT, variant TEXT, motion_key TEXT,
          reward_id TEXT, level INTEGER, event_id TEXT, file_path TEXT, filename TEXT, mime_type TEXT, sha256 TEXT, version INTEGER,
          active INTEGER, source_job_id TEXT, created_at TEXT);
        INSERT INTO character_assets VALUES ('old', 'miku', 'character_variant_png', 'development', NULL, NULL, 2, NULL, 'old.png', 'old.png', 'image/png', 'sha', 1, 1, 'job', 'now');
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    store.initialize()
    before = store.list_character_assets("miku", active_only=False)
    store.initialize()
    after = store.list_character_assets("miku", active_only=False)

    assert before[0]["asset_id"] == after[0]["asset_id"] == "old"
    assert {"generation_index"}.issubset(after[0])
    with store.connect() as check:
        columns = {row[1] for row in check.execute("PRAGMA table_info(asset_jobs)")}
    assert {"stage", "progress_value", "progress_max"} <= columns


def test_legacy_collision_allocates_g1_then_new_g2_and_keeps_files(tmp_path):
    root = tmp_path / "assets" / "characters" / "miku"
    (root / "motions" / "development").mkdir(parents=True)
    (root / "images" / "bg").mkdir(parents=True)
    legacy_motion = root / "motions" / "development" / "idle.webm"
    legacy_background = root / "images" / "bg" / "development.png"
    legacy_motion.write_bytes(b"legacy-motion")
    legacy_background.write_bytes(b"legacy-background")
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO character_assets
            (asset_id, character_id, asset_type, variant, file_path, filename,
             mime_type, sha256, version, active, source_job_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "old-background", "miku", "character_background_png", "development",
                legacy_background.as_posix(), legacy_background.name, "image/png",
                "old-sha", 1, 1, "old-job", "now",
            ),
        )

    assert store.allocate_generation("miku", "development", root) == 2
    png = root / "images" / "development" / "new.png"
    png.parent.mkdir()
    png.write_bytes(b"new")
    saved = store.insert_character_asset(GeneratedAsset(
        "miku", "character_variant_png", "development", str(png), png.name,
        "image/png", "new-sha", "job-2", generation_index=2,
    ).__dict__)
    rows = store.list_character_assets("miku", active_only=False, variant="development")

    assert saved["generation_index"] == 2
    assert {row["generation_index"] for row in rows} == {1, 2}
    assert sum(row["generation_index"] == 1 for row in rows) == 2
    background_rows = [row for row in rows if row["asset_type"] == "character_background_png"]
    assert len(background_rows) == 1
    assert background_rows[0]["asset_id"] == "old-background"
    assert legacy_motion.read_bytes() == b"legacy-motion"
    assert legacy_background.read_bytes() == b"legacy-background"
    assert len(store.list_character_assets("miku", active_only=False, variant="development")) == len(rows)


def test_resolver_keeps_one_generation_and_falls_to_og(tmp_path, monkeypatch):
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
    root = tmp_path / "assets" / "characters" / "miku"
    (root / "motions" / "development" / "g02").mkdir(parents=True)
    (root / "motions" / "development" / "g03").mkdir(parents=True)
    (root / "motions" / "og").mkdir(parents=True)
    (root / "motions" / "development" / "g02" / "idle.webm").write_bytes(b"g2-idle")
    (root / "motions" / "development" / "g02" / "walk.webm").write_bytes(b"g2-walk")
    (root / "motions" / "development" / "g03" / "idle.webm").write_bytes(b"g3-idle")
    og_walk = root / "motions" / "og" / "walk.webm"
    og_walk.write_bytes(b"og-walk")
    (root / "manifest.json").write_text(json.dumps({
        "id": "miku", "motions_dir": "assets/characters/miku/motions", "motions": {},
        "active_variant": "development", "selected_generations": {},
    }), encoding="utf-8")
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    for generation, keys in ((2, ("idle", "walk")), (3, ("idle",))):
        for key in keys:
            path = root / "motions" / "development" / f"g{generation:02d}" / f"{key}.webm"
            store.insert_character_asset({
                "asset_id": f"g{generation}-{key}", "character_id": "miku", "asset_type": "motion_webm",
                "variant": "development", "motion_key": key, "file_path": str(path), "filename": path.name,
                "mime_type": "video/webm", "sha256": "sha", "source_job_id": f"g{generation}",
            } | {"generation_index": generation})
    library = CharacterLibrary()
    library.select_style_generation("miku", "development", "g3-idle")

    assert library.get_motion_path("miku", "idle").endswith("development\\g03\\idle.webm")
    assert library.get_motion_path("miku", "walk") == str(og_walk)

    reloaded = CharacterLibrary()
    assert reloaded.get_motion_path("miku", "idle").endswith("development\\g03\\idle.webm")


def test_job_progress_and_history_are_character_scoped(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    repo = AssetRepository(store)
    job_a = repo.create_job(AssetJob("A", "variant_png", "development", "A-job"))
    job_b = repo.create_job(AssetJob("B", "variant_png", "development", "B-job"))
    repo.update(job_a.job_id, JobStatus.RUNNING, stage="rendering", progress_value=15, progress_max=20)
    repo.update(job_b.job_id, JobStatus.RUNNING, stage="downloading", progress_value=1, progress_max=4)

    jobs_a = store.list_asset_jobs("A")
    assert [job["job_id"] for job in jobs_a] == [job_a.job_id]
    assert (jobs_a[0]["stage"], jobs_a[0]["progress_value"], jobs_a[0]["progress_max"]) == ("rendering", 15, 20)
    assert store.list_asset_jobs("B")[0]["job_id"] == job_b.job_id


def test_character_switch_rehydrates_selection_without_cross_character_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")

    stores = {}
    for character_id, generations in (("A", (1, 2)), ("B", (1,))):
        root = tmp_path / "assets" / "characters" / character_id
        for generation in generations:
            motion = root / "motions" / "development" / f"g{generation:02d}" / "idle.webm"
            motion.parent.mkdir(parents=True, exist_ok=True)
            motion.write_bytes(f"{character_id}-g{generation}".encode())
        (root / "manifest.json").parent.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(json.dumps({
            "id": character_id,
            "motions_dir": f"assets/characters/{character_id}/motions",
            "motions": {},
            "active_variant": "development",
            "selected_generations": {},
        }), encoding="utf-8")
        store = SQLiteStore(tmp_path / "data" / "characters" / character_id / "state.db")
        store.initialize()
        for generation in generations:
            path = root / "motions" / "development" / f"g{generation:02d}" / "idle.webm"
            store.insert_character_asset({
                "asset_id": f"{character_id}-g{generation}",
                "character_id": character_id,
                "asset_type": "motion_webm",
                "variant": "development",
                "motion_key": "idle",
                "file_path": str(path),
                "filename": path.name,
                "mime_type": "video/webm",
                "sha256": "sha",
                "source_job_id": f"{character_id}-g{generation}",
                "generation_index": generation,
            })
        stores[character_id] = store

    library = CharacterLibrary()
    library.select_style_generation("A", "development", "A-g1")
    library.select_style_generation("B", "development", "B-g1")
    assert library.get_motion_path("A", "idle").endswith("A\\motions\\development\\g01\\idle.webm")
    assert library.get_motion_path("B", "idle").endswith("B\\motions\\development\\g01\\idle.webm")

    library.select_style_generation("A", "development", "A-g2")
    assert library.get_motion_path("A", "idle").endswith("A\\motions\\development\\g02\\idle.webm")
    assert library.get_motion_path("B", "idle").endswith("B\\motions\\development\\g01\\idle.webm")

    job_a = AssetRepository(stores["A"]).create_job(AssetJob("A", "motion_clip", "development", "A-render"))
    job_b = AssetRepository(stores["B"]).create_job(AssetJob("B", "motion_clip", "development", "B-render"))
    AssetRepository(stores["A"]).update(job_a.job_id, JobStatus.RUNNING, stage="rendering", progress_value=16, progress_max=20)
    AssetRepository(stores["B"]).update(job_b.job_id, JobStatus.RUNNING, stage="rendering", progress_value=2, progress_max=20)
    assert [job["job_id"] for job in stores["A"].list_asset_jobs("A")] == [job_a.job_id]
    assert [job["job_id"] for job in stores["B"].list_asset_jobs("B")] == [job_b.job_id]


def test_comfy_progress_forwards_real_ws_values_and_polling_is_indeterminate(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.messages = iter([
                json.dumps({"type": "progress", "data": {"prompt_id": "p", "value": 15, "max": 20}}),
                json.dumps({"type": "executing", "data": {"prompt_id": "p", "node": None}}),
            ])

        def recv(self):
            return next(self.messages)

        def close(self):
            pass

    socket = FakeSocket()
    monkeypatch.setitem(sys.modules, "websocket", types.SimpleNamespace(create_connection=lambda *args, **kwargs: socket))
    client = ComfyUIClient("http://comfy", session=types.SimpleNamespace())
    monkeypatch.setattr(client, "get_history", lambda _prompt: {"outputs": {"1": {}}})
    seen = []
    assert client.watch_prompt("p", 1, on_progress=lambda value, maximum: seen.append((value, maximum))) == {"outputs": {"1": {}}}
    assert seen == [(15, 20)]

    class BrokenSocket:
        def recv(self):
            raise OSError("socket closed")

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "websocket", types.SimpleNamespace(create_connection=lambda *args, **kwargs: BrokenSocket()))
    monkeypatch.setattr(client, "get_history", lambda _prompt: {"outputs": {"1": {}}})
    seen.clear()
    assert client.watch_prompt("p", 1, on_progress=lambda value, maximum: seen.append((value, maximum))) == {"outputs": {"1": {}}}
    assert seen == [(None, None)]
