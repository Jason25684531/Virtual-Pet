import json

import character_library as library_module
from character_library import CharacterLibrary


def _library(tmp_path, monkeypatch):
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
    monkeypatch.setattr(library_module, "UI_MUSIC_DIR", tmp_path / "ui" / "assets" / "music")
    return CharacterLibrary()


def _write_manifest(tmp_path, character_id, motions, actions=None):
    character_dir = tmp_path / "assets" / "characters" / character_id
    motion_dir = character_dir / "motions"
    motion_dir.mkdir(parents=True)
    for key in motions:
        (motion_dir / f"{key}.webm").write_bytes(b"webm")
    manifest = {
        "id": character_id, "name": character_id,
        "motions_dir": f"assets/characters/{character_id}/motions",
        "motions": {key: f"assets/characters/{character_id}/motions/{key}.webm" for key in motions},
    }
    if actions is not None:
        manifest["actions"] = actions
    (character_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_action_tags_fall_back_to_non_idle_manifest_motions(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    _write_manifest(tmp_path, "char-lei-jie", ["idle", "laugh", "angry", "awkward", "speechless", "listen", "wave_response"])

    assert library.list_action_tags("char-lei-jie") == ["laugh", "angry", "awkward", "speechless", "listen", "wave_response"]
    assert library.resolve_action_tag("char-lei-jie", "laugh")["motion_key"] == "laugh"


def test_explicit_manifest_actions_take_precedence_over_motion_fallback(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    _write_manifest(tmp_path, "miku", ["idle", "laugh", "wave"], {"greet": "wave"})

    assert library.list_action_tags("miku") == ["greet"]
    assert library.resolve_action_tag("miku", "greet")["motion_key"] == "wave"
