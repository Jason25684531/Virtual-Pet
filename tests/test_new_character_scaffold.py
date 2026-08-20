import json
import runpy
from pathlib import Path

import character_library as library_module
from character_library import CharacterLibrary
import pet_harness.character.profile as profile_module
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter


def test_scaffold_loads_manual_character_and_flat_assets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
    monkeypatch.setattr(library_module, "UI_MUSIC_DIR", tmp_path / "ui" / "assets" / "music")
    monkeypatch.setattr(profile_module, "_PROJECT_ROOT", tmp_path)

    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "new_character.py"))
    assert namespace["main"](["char-ming", "Ming"]) == 0

    root = tmp_path / "assets" / "characters" / "char-ming"
    (root / "images" / "og" / "char-ming.png").write_bytes(b"png")
    (root / "motions" / "og" / "idle.webm").write_bytes(b"webm")
    personal_path = tmp_path / "data" / "characters" / "char-ming" / "personal.json"
    personal = json.loads(personal_path.read_text(encoding="utf-8"))
    personal["skill_refs"] = ["game_news"]
    personal_path.write_text(json.dumps(personal), encoding="utf-8")

    library = CharacterLibrary()
    assert any(item["id"] == "char-ming" for item in library.list_characters())
    inventory = library.list_variant_inventory("char-ming")
    assert inventory[0]["variant"] == "og"
    assert inventory[0]["state"] == "ready"

    router = CharacterRouter(
        registry=CharacterRegistry(
            assets_dir=str(tmp_path / "assets" / "webm" / "characters"),
            data_dir=str(tmp_path / "data" / "characters"),
        ),
        agentic_root=str(tmp_path / ".agentic"),
    )
    profile, is_library = router.load_profile("char-ming")
    assert is_library is True
    assert profile.skill_config == ["game_news"]


def test_scaffold_rejects_invalid_and_duplicate_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "new_character.py"))
    main = namespace["main"]

    try:
        main(["bad/id", "Bad"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("invalid id was accepted")

    assert main(["char-ok", "Okay"]) == 0
    try:
        main(["char-ok", "Again"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("duplicate id was accepted")
