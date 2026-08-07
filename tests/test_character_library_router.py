from pathlib import Path

import character_library as library_module
from character_library import CharacterLibrary
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter


def test_router_uses_character_library_character_and_its_state_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    source = tmp_path / "og.png"
    source.write_bytes(b"og")
    CharacterLibrary().create_validated_character("char_1234", str(source), "丘比")
    (tmp_path / ".agentic" / "skills").mkdir(parents=True)

    router = CharacterRouter(registry=CharacterRegistry(assets_dir=str(tmp_path / "legacy"), data_dir=str(tmp_path / "data" / "characters")), agentic_root=str(tmp_path / ".agentic"))
    profile = router.switch_character("char_1234")

    assert profile.name == "丘比"
    assert router.get_active_engine().store.db_path == Path("data/characters/char_1234/state.db")


def test_playtime_and_listing_work_for_library_characters(tmp_path, monkeypatch):
    """崩潰回歸(2026-08-07):add_playtime 對 library 角色拋 CharacterNotFoundError。"""
    from pet_harness.storage.sqlite_store import SQLiteStore
    from pet_harness.ui.character_ui_service import PLAYTIME_SECONDS_KEY, CharacterUiService

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    source = tmp_path / "og.png"
    source.write_bytes(b"og")
    CharacterLibrary().create_validated_character("char-ya-zhou-tong-shen", str(source), "亞洲統神")

    registry = CharacterRegistry(assets_dir=str(tmp_path / "legacy"), data_dir=str(tmp_path / "data" / "characters"))
    router = CharacterRouter(registry=registry, agentic_root=str(tmp_path / ".agentic"))
    service = CharacterUiService(router=router, registry=registry, customization_service=object())

    service.add_playtime("char-ya-zhou-tong-shen", 7)
    store = SQLiteStore(Path("data/characters/char-ya-zhou-tong-shen/state.db"))
    store.initialize()
    assert int(store.get_setting(PLAYTIME_SECONDS_KEY, 0)) == 7

    listed = service.list_characters()
    assert any(item["character_id"] == "char-ya-zhou-tong-shen" and item["name"] == "亞洲統神" for item in listed)
