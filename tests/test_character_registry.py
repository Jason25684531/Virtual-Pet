import json

import pytest

import pet_harness.character.profile as profile_module
from pet_harness.character.exceptions import (
    CharacterNotFoundError,
)
from pet_harness.character.registry import CharacterRegistry


def _write_character(root, character_id, name, voice_key=""):
    """在 tmp 根目錄下建立一個雙檔案齊全的測試角色。"""
    assets_dir = root / "assets" / "webm" / "characters" / character_id
    (assets_dir / "motions").mkdir(parents=True)
    manifest = {
        "id": character_id,
        "name": name,
        "background_image": "",
        "motions_dir": f"assets/webm/characters/{character_id}/motions",
        "motions": {"idle": f"assets/webm/characters/{character_id}/motions/Idle.webm"},
        "idle_pool": [{"motion": "idle", "weight": 1}],
        "voice_id_env_key": voice_key,
        "layout": {},
    }
    (assets_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    data_dir = root / "data" / "characters" / character_id
    data_dir.mkdir(parents=True)
    profile = {
        "persona_description": f"{name} persona",
        "skill_config": ["mood_skill"],
    }
    (data_dir / "profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """tmp 根目錄下建好 Choppr / miku，並讓 CharacterProfile 與 registry 同源。"""
    _write_character(tmp_path, "Choppr", "喬巴", voice_key="CHOPPER_VOICE_ID")
    _write_character(tmp_path, "miku", "初音未來")
    monkeypatch.setattr(profile_module, "_PROJECT_ROOT", tmp_path)
    return CharacterRegistry(
        assets_dir=str(tmp_path / "assets" / "webm" / "characters"),
        data_dir=str(tmp_path / "data" / "characters"),
    )


class TestLoadCharacter:
    def test_load_choppr(self, registry):
        p = registry.load_character("Choppr")
        assert p.character_id == "Choppr"
        assert p.name == "喬巴"
        assert p.voice_id_env_key == "CHOPPER_VOICE_ID"

    def test_load_not_found_raises(self, registry):
        with pytest.raises(CharacterNotFoundError):
            registry.load_character("ghost")


class TestListCharacters:
    def test_list_characters(self, registry):
        chars = registry.list_characters()
        assert len(chars) == 2
        assert sorted(c.character_id for c in chars) == ["Choppr", "miku"]


class TestDeleteCharacter:
    def test_delete_character(self, registry, tmp_path):
        registry.delete_character("miku")
        assert not (tmp_path / "assets" / "webm" / "characters" / "miku").exists()
        assert not (tmp_path / "data" / "characters" / "miku").exists()

    def test_delete_missing_raises(self, registry):
        with pytest.raises(CharacterNotFoundError):
            registry.delete_character("ghost")
