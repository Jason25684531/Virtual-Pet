import json
import os
from pathlib import Path

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

    assert library.list_action_tags("char-lei-jie") == ["laugh", "annoy", "awkward", "speechless", "listen", "waving"]
    assert library.resolve_action_tag("char-lei-jie", "laugh")["motion_key"] == "laugh"
    assert library.resolve_action_tag("char-lei-jie", "annoy")["motion_key"] == "angry"
    assert library.resolve_action_tag("char-lei-jie", "waving")["motion_key"] == "wave_response"


def test_legacy_action_tags_still_resolve_to_canonical_actions(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    _write_manifest(tmp_path, "char-lei-jie", ["idle", "angry", "wave_response"])

    assert library.resolve_action_tag("char-lei-jie", "angry")["action_tag"] == "annoy"
    assert library.resolve_action_tag("char-lei-jie", "wave_response")["action_tag"] == "waving"


def test_native_canonical_manifest_action_wins_over_legacy_alias(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    _write_manifest(
        tmp_path,
        "miku",
        ["idle", "angry", "annoy"],
        {"angry": "angry", "annoy": "annoy"},
    )

    assert library.list_action_tags("miku") == ["annoy"]
    assert library.resolve_action_tag("miku", "annoy")["motion_key"] == "annoy"


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


def test_prebuilt_variant_directory_wins_over_stale_manifest_motion(tmp_path, monkeypatch):
    """Adol-style characters keep every usable motion set in a variant directory."""
    library = _library(tmp_path, monkeypatch)
    character_dir = tmp_path / "assets" / "characters" / "char-Adol"
    motions = character_dir / "motions"
    for variant in ("og", "development_a", "development_b", "event"):
        (motions / variant).mkdir(parents=True, exist_ok=True)
        (motions / variant / "idle.webm").write_bytes(variant.encode())
        (motions / variant / "annoy.webm").write_bytes(variant.encode())
        (motions / variant / "wave_response.webm").write_bytes(variant.encode())
    (character_dir / "manifest.json").write_text(json.dumps({
        "id": "char-Adol", "motions_dir": "assets/characters/char-Adol/motions",
        # This is a stale flat mapping from the last applied event style.
        "motions": {"idle": "assets/characters/char-Adol/motions/event/idle.webm"},
        "active_variant": "og", "selected_generations": {},
    }), encoding="utf-8")

    for variant in ("og", "development_a", "development_b", "event"):
        library.set_active_variant("char-Adol", variant)
        assert Path(library.get_motion_path("char-Adol", "idle")).read_bytes() == variant.encode()
        assert Path(library.get_motion_path("char-Adol", "angry")).read_bytes() == variant.encode()


def test_reset_style_state_returns_to_og_and_forgets_selected_generations(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    character_dir = tmp_path / "assets" / "characters" / "pet"
    (character_dir / "motions" / "og").mkdir(parents=True)
    (character_dir / "motions" / "og" / "idle.webm").write_bytes(b"og")
    (character_dir / "manifest.json").write_text(json.dumps({
        "id": "pet", "motions_dir": "assets/characters/pet/motions", "motions": {},
        "active_variant": "event", "selected_generations": {"event": {"generation": 2}},
    }), encoding="utf-8")

    manifest = library.reset_style_state("pet")

    assert manifest["active_variant"] == "og"
    assert manifest["selected_generations"] == {}


def test_generation_miss_falls_back_to_manifest_motion_path(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    character_dir = tmp_path / "assets" / "characters" / "omni"
    motion_dir = character_dir / "motions" / "og"
    motion_dir.mkdir(parents=True)
    motion = motion_dir / "Laughing.webm"
    motion.write_bytes(b"webm")
    (character_dir / "manifest.json").write_text(json.dumps({
        "id": "omni", "motions_dir": "assets/characters/omni/motions/og",
        "motions": {"laugh": "assets/characters/omni/motions/og/Laughing.webm"},
        "active_variant": "og",
    }), encoding="utf-8")
    monkeypatch.setattr(library, "_selected_wearable_generation", lambda *_args: "revision-1")
    monkeypatch.setattr(library, "_generation_motion_path", lambda *_args: None)

    assert library.get_motion_path("omni", "laugh") == str(motion)


def test_new_character_defaults_to_follow_background_mode(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    source = tmp_path / "source.png"
    source.write_bytes(b"png")

    manifest = library.create_validated_character("char-1", str(source), "Character")

    assert manifest["background_mode"] == "follow"
    assert library.get_background_mode("char-1") == "follow"


def test_legacy_manifest_without_background_mode_defaults_to_follow(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    _write_manifest(tmp_path, "legacy", [])

    assert library.get_background_mode("legacy") == "follow"


def test_set_background_mode_persists(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    source = tmp_path / "source.png"
    source.write_bytes(b"png")
    library.create_validated_character("char-1", str(source), "Character")

    manifest = library.set_background_mode("char-1", "manual")

    assert manifest["background_mode"] == "manual"
    assert library.get_background_mode("char-1") == "manual"


def test_list_background_scenes_reports_available_variant_backgrounds(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    character_dir = tmp_path / "assets" / "characters" / "miku"
    bg_dir = character_dir / "images" / "bg"
    bg_dir.mkdir(parents=True)
    (bg_dir / "og.png").write_bytes(b"og")
    (bg_dir / "event.png").write_bytes(b"event")
    (character_dir / "manifest.json").write_text(json.dumps({
        "id": "miku", "motions_dir": "assets/characters/miku/motions", "motions": {},
        "active_variant": "og", "background_image": "assets/characters/miku/images/bg/og.png",
    }), encoding="utf-8")

    scenes = {item["scene_id"]: item for item in library.list_background_scenes("miku")}

    assert set(scenes) == {"og", "event"}
    assert scenes["og"]["is_current"] is True
    assert scenes["event"]["is_current"] is False


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
