import json
import os

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
    _write_manifest(tmp_path, "miku", ["idle", "laugh", "wave"], {"greet": "wave", "idle": "idle"})

    assert library.list_action_tags("miku") == ["greet"]
    assert library.resolve_action_tag("miku", "greet")["motion_key"] == "wave"
    assert library.resolve_action_tag("miku", "idle") is None


def test_validated_character_persists_and_reads_voice_gender(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    source = tmp_path / "source.png"
    source.write_bytes(b"png")

    manifest = library.create_validated_character("char-1", str(source), "Character", "F")

    assert manifest["voice_gender"] == "F"
    assert library.get_voice_gender("char-1") == "F"
    _write_manifest(tmp_path, "legacy", [])
    assert library.get_voice_gender("legacy") == ""


def test_variant_inventory_uses_png_preview_until_idle_motion_is_ready(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    character_dir = tmp_path / "assets" / "characters" / "miku"
    (character_dir / "images" / "event").mkdir(parents=True)
    (character_dir / "images" / "event" / "preview.png").write_bytes(b"png")
    (character_dir / "motions" / "event").mkdir(parents=True)
    (character_dir / "manifest.json").write_text(json.dumps({
        "id": "miku", "name": "Miku", "motions_dir": "assets/characters/miku/motions",
        "motions": {}, "active_variant": "event",
    }), encoding="utf-8")

    assert library.get_motion_path("miku", "idle") is None
    assert library.list_variant_inventory("miku") == [{
        "variant": "event", "state": "generating",
        "thumb": "assets/characters/miku/images/event/preview.png", "is_active": True,
    }]

    (character_dir / "motions" / "event" / "idle.webm").write_bytes(b"webm")
    assert library.get_motion_path("miku", "idle").endswith("motions\\event\\idle.webm")
    assert library.list_variant_inventory("miku")[0]["state"] == "ready"


def test_motion_resolution_prefers_active_then_flat_manifest_then_og(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    character_dir = tmp_path / "assets" / "characters" / "miku"
    motions = character_dir / "motions"
    for variant in ("event", "og"):
        (motions / variant).mkdir(parents=True, exist_ok=True)
        (motions / variant / "idle.webm").write_bytes(variant.encode())
    flat = motions / "idle.webm"
    flat.write_bytes(b"flat")
    (character_dir / "manifest.json").write_text(json.dumps({
        "id": "miku", "motions_dir": "assets/characters/miku/motions",
        "motions": {"idle": "assets/characters/miku/motions/idle.webm"},
        "active_variant": "event",
    }), encoding="utf-8")

    assert library.get_motion_path("miku", "idle").endswith("motions\\event\\idle.webm")
    (motions / "event" / "idle.webm").unlink()
    assert library.get_motion_path("miku", "idle") == str(flat)
    flat.unlink()
    assert library.get_motion_path("miku", "idle").endswith("motions\\og\\idle.webm")


def test_variant_inventory_uses_newest_png_as_thumbnail(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    character_dir = tmp_path / "assets" / "characters" / "miku"
    images = character_dir / "images" / "event"
    images.mkdir(parents=True)
    (images / "old.png").write_bytes(b"old")
    (images / "new.png").write_bytes(b"new")
    os.utime(images / "old.png", (1, 1))
    os.utime(images / "new.png", (2, 2))
    (character_dir / "manifest.json").write_text(json.dumps({
        "id": "miku", "motions_dir": "assets/characters/miku/motions",
        "motions": {}, "active_variant": "og",
    }), encoding="utf-8")

    assert library.list_variant_inventory("miku")[0]["thumb"].endswith("new.png")
