import pytest

import character_library as library_module
import config
from character_library import CharacterLibrary


@pytest.mark.parametrize(("gender", "voice_key"), [("F", "miku"), ("M", "Choppr"), ("", None)])
def test_generated_character_voice_uses_manifest_gender(tmp_path, monkeypatch, gender, voice_key):
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
    monkeypatch.setattr(library_module, "UI_MUSIC_DIR", tmp_path / "ui" / "assets" / "music")
    source = tmp_path / "source.png"
    source.write_bytes(b"png")
    CharacterLibrary().create_validated_character("char-generated", str(source), "Generated", gender)

    expected_voai = config.CHARACTER_VOAI_CONFIGS[voice_key] if voice_key else config._DEFAULT_VOAI_CONFIG
    expected_elevenlabs = config.CHARACTER_VOICE_IDS[voice_key] if voice_key else config.ELEVENLABS_VOICE_ID
    assert config.get_voai_config_for_character("char-generated") == expected_voai
    assert config.get_elevenlabs_voice_id_for_character("char-generated") == expected_elevenlabs


def test_builtin_character_voice_mappings_are_unchanged():
    assert config.get_voai_config_for_character("miku") == config.CHARACTER_VOAI_CONFIGS["miku"]
    assert config.get_voai_config_for_character("Choppr") == config.CHARACTER_VOAI_CONFIGS["Choppr"]
    assert config.get_elevenlabs_voice_id_for_character("miku") == config.CHARACTER_VOICE_IDS["miku"]
    assert config.get_elevenlabs_voice_id_for_character("Choppr") == config.CHARACTER_VOICE_IDS["Choppr"]


def test_builtin_character_voice_mappings_use_dedicated_ids(monkeypatch):
    for env_keys in config.CHARACTER_ELEVENLABS_VOICE_ENV_KEYS.values():
        for env_key in env_keys:
            monkeypatch.delenv(env_key, raising=False)
    for character_id in config.BUILTIN_CHARACTER_ELEVENLABS_VOICE_IDS:
        monkeypatch.delenv(config.character_voice_env_key(character_id), raising=False)
    monkeypatch.setattr(config, "CHARACTER_VOICE_IDS", config._build_character_elevenlabs_voice_ids())

    for character_id, voice_id in config.BUILTIN_CHARACTER_ELEVENLABS_VOICE_IDS.items():
        assert config.get_elevenlabs_voice_id_for_character(character_id) == voice_id


def test_character_voice_env_override_wins(monkeypatch):
    override = "voice-from-env"
    monkeypatch.setenv("ELEVENLABS_CHAR_ADOL_VOICE_ID", override)
    monkeypatch.setattr(config, "CHARACTER_VOICE_IDS", config._build_character_elevenlabs_voice_ids())

    assert config.get_elevenlabs_voice_id_for_character("char-Adol") == override


@pytest.mark.parametrize(
    ("character_id", "gender", "voice_key"),
    [
        ("char-asuna", "F", "miku"),
        ("char-little-zi", "M", "Choppr"),
        ("char-RO", "F", "miku"),
        ("char-siei", "M", "Choppr"),
        ("char-ya-zhou-tong-shen", "F", "miku"),
    ],
)
def test_unmapped_character_voice_uses_gender_fallback(monkeypatch, character_id, gender, voice_key):
    monkeypatch.setattr(CharacterLibrary, "get_voice_gender", lambda self, _: gender)

    assert config.get_elevenlabs_voice_id_for_character(character_id) == config.CHARACTER_VOICE_IDS[voice_key]


def test_rog_voice_mapping_does_not_apply_to_ro(monkeypatch):
    monkeypatch.setattr(CharacterLibrary, "get_voice_gender", lambda self, _: "F")

    assert config.get_elevenlabs_voice_id_for_character("char-ROG") == "RoUNDCtoHQPMwQQoROwA"
    assert config.get_elevenlabs_voice_id_for_character("char-RO") == config.CHARACTER_VOICE_IDS["miku"]
