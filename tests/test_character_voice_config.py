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
