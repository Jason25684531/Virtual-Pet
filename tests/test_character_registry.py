import json

import pytest

import pet_harness.character.profile as profile_module
from pet_harness.character.exceptions import (
    CharacterAlreadyExistsError,
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


class TestCreateCharacter:
    def test_create_character(self, registry, tmp_path):
        result = registry.create_character(
            "test_char",
            name="Test",
            persona_description="a test character",
            skill_config=["mood_skill"],
            voice_id_env_key="TEST_VOICE_ID",
        )
        assert result.character_id == "test_char"
        assets_dir = tmp_path / "assets" / "webm" / "characters" / "test_char"
        data_dir = tmp_path / "data" / "characters" / "test_char"
        assert (assets_dir / "manifest.json").exists()
        assert (assets_dir / "motions").is_dir()
        assert (data_dir / "profile.json").exists()

        manifest = json.loads(
            (assets_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["motions"] == {}

    def test_create_duplicate_raises(self, registry):
        with pytest.raises(CharacterAlreadyExistsError):
            registry.create_character(
                "Choppr",
                name="Dup",
                persona_description="dup",
                skill_config=[],
                voice_id_env_key="",
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
        registry.create_character(
            "test_char",
            name="Test",
            persona_description="tmp",
            skill_config=[],
            voice_id_env_key="",
        )
        registry.delete_character("test_char")
        assert not (tmp_path / "assets" / "webm" / "characters" / "test_char").exists()
        assert not (tmp_path / "data" / "characters" / "test_char").exists()

    def test_delete_active_clears_active(self, registry):
        registry.set_active("Choppr")
        registry.delete_character("Choppr")
        assert registry.get_active() is None


class TestActiveCharacter:
    def test_set_and_get_active(self, registry):
        registry.set_active("miku")
        assert registry.get_active().character_id == "miku"

    def test_set_active_not_found(self, registry):
        with pytest.raises(CharacterNotFoundError):
            registry.set_active("ghost")


class TestUpdateManifest:
    def test_update_manifest_does_not_touch_profile(self, registry, tmp_path):
        manifest_path = (
            tmp_path / "assets" / "webm" / "characters" / "Choppr" / "manifest.json"
        )
        profile_path = tmp_path / "data" / "characters" / "Choppr" / "profile.json"
        profile_before = profile_path.read_text(encoding="utf-8")
        old_motions = json.loads(manifest_path.read_text(encoding="utf-8"))["motions"]

        registry.update_manifest(
            "Choppr",
            {"motions": {**old_motions, "new_motion": "path/to/new.webm"}},
        )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "new_motion" in manifest["motions"]
        assert "idle" in manifest["motions"]
        assert profile_path.read_text(encoding="utf-8") == profile_before
