import json
from pathlib import Path

import pytest

import pet_harness.character.profile as profile_module
from pet_harness.character.exceptions import CharacterNotFoundError, NoActiveCharacterError
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.ui.character_ui_service import LAST_PLAYED_AT_KEY, CharacterUiService

_SKILL_FIXTURES = {
    "joke_skill": {"trigger": "joke, funny", "behavior": "laugh", "xp_reward": "5"},
    "mood_skill": {"trigger": "mood, feeling", "behavior": "idle", "xp_reward": "3"},
    "music_skill": {"trigger": "music, bgm", "behavior": "play_music", "xp_reward": "4"},
}


def _write_skill(skills_dir: Path, name: str, meta: dict[str, str]) -> None:
    lines = [
        f"name: {name}",
        f"description: fixture skill {name}",
        f"trigger: {meta['trigger']}",
        f"behavior: {meta['behavior']}",
        f"xp_reward: {meta['xp_reward']}",
    ]
    (skills_dir / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_character(
    root: Path,
    character_id: str,
    skill_config: list[str],
    is_preset: bool = False,
    motions: dict[str, str] | None = None,
) -> None:
    assets_dir = root / "assets" / "webm" / "characters" / character_id
    (assets_dir / "motions").mkdir(parents=True)
    manifest = {
        "id": character_id,
        "name": character_id,
        "background_image": f"assets/webm/characters/{character_id}/bg.png",
        "motions_dir": f"assets/webm/characters/{character_id}/motions",
        "motions": motions or {"idle": f"assets/webm/characters/{character_id}/motions/idle.webm"},
        "idle_pool": [{"motion": "idle", "weight": 1}],
        "voice_id_env_key": "",
        "layout": {},
        "is_preset": is_preset,
    }
    (assets_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    data_dir = root / "data" / "characters" / character_id
    data_dir.mkdir(parents=True)
    profile = {
        "persona_description": f"{character_id} persona",
        "skill_config": skill_config,
    }
    (data_dir / "profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.fixture
def service(tmp_path, monkeypatch):
    """tmp 根目錄下建好 Choppr（preset）/ miku（preset），回傳 (service, router, registry)。"""
    _write_character(tmp_path, "Choppr", ["joke_skill", "mood_skill"], is_preset=True)
    _write_character(tmp_path, "miku", ["music_skill"], is_preset=True)
    monkeypatch.setattr(profile_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    agentic_root = tmp_path / ".agentic"
    skills_dir = agentic_root / "skills"
    skills_dir.mkdir(parents=True)
    for name, meta in _SKILL_FIXTURES.items():
        _write_skill(skills_dir, name, meta)

    registry = CharacterRegistry(
        assets_dir=str(tmp_path / "assets" / "webm" / "characters"),
        data_dir=str(tmp_path / "data" / "characters"),
    )
    router = CharacterRouter(registry=registry, agentic_root=str(agentic_root))
    return CharacterUiService(router=router, registry=registry), router, registry


class TestListCharacters:
    def test_list_characters_includes_is_preset_and_xp(self, service):
        ui_service, _router, _registry = service
        items = {item["character_id"]: item for item in ui_service.list_characters()}

        assert items["Choppr"]["is_preset"] is True
        assert items["miku"]["is_preset"] is True
        assert items["Choppr"]["xp_total"] == 0
        assert items["Choppr"]["level"] == 1

    def test_list_presets_filters_to_preset_only(self, service, tmp_path):
        ui_service, _router, _registry = service
        _write_character(tmp_path, "Choppr_1", ["joke_skill"], is_preset=False)

        preset_ids = {item["character_id"] for item in ui_service.list_presets()}
        assert preset_ids == {"Choppr", "miku"}


class TestCreateFromPreset:
    def test_create_from_choppr_preset_switches_directly_without_copy(self, service):
        ui_service, router, registry = service
        result = ui_service.create_from_preset("Choppr")

        assert result["character_id"] == "Choppr"
        assert router.get_active_character().character_id == "Choppr"
        new_profile = router.get_active_character()
        assert new_profile.skill_config == ["joke_skill", "mood_skill"]
        assert new_profile.is_preset is True
        assert new_profile.motions == registry.load_character("Choppr").motions

    def test_create_from_preset_is_idempotent_no_new_dirs(self, service, tmp_path):
        ui_service, _router, _registry = service
        first = ui_service.create_from_preset("Choppr")
        second = ui_service.create_from_preset("Choppr")

        assert first["character_id"] == "Choppr"
        assert second["character_id"] == "Choppr"
        assert not (tmp_path / "assets" / "webm" / "characters" / "Choppr_1").exists()
        assert not (tmp_path / "data" / "characters" / "Choppr_1").exists()

    def test_create_from_unknown_preset_raises_and_no_dirs(self, service, tmp_path):
        ui_service, _router, _registry = service

        with pytest.raises(CharacterNotFoundError):
            ui_service.create_from_preset("ghost")

        assert not (tmp_path / "assets" / "webm" / "characters" / "ghost").exists()
        assert not (tmp_path / "data" / "characters" / "ghost").exists()


class TestSwitchAndDelete:
    def test_switch_character_delegates_to_router(self, service):
        ui_service, router, _registry = service
        result = ui_service.switch_character("miku")

        assert result["character_id"] == "miku"
        assert router.get_active_character().character_id == "miku"

    def test_delete_preset_raises_value_error(self, service, tmp_path):
        ui_service, _router, _registry = service

        with pytest.raises(ValueError):
            ui_service.delete_character("Choppr")

        assert (tmp_path / "assets" / "webm" / "characters" / "Choppr").exists()

    def test_switch_character_writes_last_played_at(self, service):
        ui_service, router, _registry = service
        ui_service.switch_character("miku")

        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        assert store.get_setting(LAST_PLAYED_AT_KEY)


class TestGetActiveState:
    def test_no_active_character_returns_inactive_state(self, service):
        ui_service, _router, _registry = service
        assert ui_service.get_active_state() == {"active": False}

    def test_active_state_reflects_active_character(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")

        state = ui_service.get_active_state()

        assert state["active"] is True
        assert state["character_id"] == "Choppr"
        assert state["xp_total"] == 0
        assert state["level"] == 1
        skill_names = {item["skill_id"] for item in state["skills"]}
        assert skill_names == {"joke_skill", "mood_skill"}

    def test_switching_character_updates_active_state(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        router.get_active_engine().store.add_user_xp(72)

        router.switch_character("miku")
        state = ui_service.get_active_state()

        assert state["character_id"] == "miku"
        assert state["xp_total"] == 0
        skill_names = {item["skill_id"] for item in state["skills"]}
        assert skill_names == {"music_skill"}
        assert "joke_skill" not in skill_names


class TestTriggerSkill:
    def test_trigger_skill_owned_by_active_character_dispatches(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")

        result = ui_service.trigger_skill("joke_skill")

        assert result["matched_skill"] == "joke_skill"
        assert router.get_active_engine().store.get_user_progress()["xp_total"] > 0

    def test_trigger_skill_not_in_active_skill_config_raises(self, service):
        ui_service, router, _registry = service
        router.switch_character("miku")

        with pytest.raises(ValueError):
            ui_service.trigger_skill("joke_skill")

        assert router.get_active_engine().store.get_user_progress()["xp_total"] == 0

    def test_trigger_skill_without_active_character_raises(self, service):
        ui_service, _router, _registry = service

        with pytest.raises(NoActiveCharacterError):
            ui_service.trigger_skill("joke_skill")
