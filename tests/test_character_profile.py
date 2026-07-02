import os
import time

import pytest

from pet_harness.character import CharacterProfile, InvalidCharacterIdError


class TestLoadChoppr:
    def test_load_choppr(self):
        p = CharacterProfile.load("Choppr")
        assert p.character_id == "Choppr"
        assert p.name == "喬巴"
        assert "idle" in p.motions
        assert "laugh" in p.motions
        assert p.sqlite_path == "data/characters/Choppr/state.db"
        assert p.qdrant_collection == "Choppr_memory"
        assert p.voice_id_env_key == "CHOPPER_VOICE_ID"
        assert isinstance(p.idle_pool, list)
        assert len(p.idle_pool) > 0


class TestLoadMiku:
    def test_load_miku(self):
        p = CharacterProfile.load("miku")
        assert p.character_id == "miku"
        assert p.name == "初音未來"
        assert "idle" in p.motions
        assert p.voice_id_env_key == ""
        assert p.sqlite_path == "data/characters/miku/state.db"
        assert p.qdrant_collection == "miku_memory"


class TestLoadMissing:
    def test_load_missing_manifest(self):
        with pytest.raises(FileNotFoundError):
            CharacterProfile.load("ghost")


class TestSave:
    def test_save_does_not_touch_manifest(self):
        p = CharacterProfile.load("Choppr")
        manifest_path = os.path.join(
            "assets", "webm", "characters", "Choppr", "manifest.json"
        )
        mtime_before = os.path.getmtime(manifest_path)
        original_desc = p.persona_description

        p.persona_description = "save_test_temporary"
        time.sleep(0.05)
        p.save()
        mtime_after = os.path.getmtime(manifest_path)

        assert mtime_before == mtime_after

        # 還原
        p.persona_description = original_desc
        p.save()


class TestSerializationRoundtrip:
    def test_roundtrip(self):
        p = CharacterProfile.load("Choppr")
        json_str = p.to_json()
        p2 = CharacterProfile.from_json(json_str)

        assert p.character_id == p2.character_id
        assert p.name == p2.name
        assert p.background_image == p2.background_image
        assert p.motions_dir == p2.motions_dir
        assert p.motions == p2.motions
        assert p.idle_pool == p2.idle_pool
        assert p.voice_id_env_key == p2.voice_id_env_key
        assert p.layout == p2.layout
        assert p.persona_description == p2.persona_description
        assert p.skill_config == p2.skill_config
        assert p.sqlite_path == p2.sqlite_path
        assert p.qdrant_collection == p2.qdrant_collection


class TestInvalidCharacterId:
    @pytest.mark.parametrize(
        "bad_id",
        [
            "My Character!",
            "my char",
            "hello@world",
            "a/b",
            "",
        ],
    )
    def test_invalid_ids(self, bad_id):
        with pytest.raises(InvalidCharacterIdError):
            CharacterProfile(
                character_id=bad_id,
                name="test",
                background_image="",
                motions_dir="",
                motions={},
                idle_pool=[],
                voice_id_env_key="",
                layout={},
                persona_description="",
                skill_config=[],
            )
