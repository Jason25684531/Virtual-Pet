"""任務 2.5:CharacterCustomizationService — 驗證式寫入、rollback、active-only refresh、角色隔離。"""

import json
from pathlib import Path

import pytest

import character_library as library_module
import pet_harness.character.profile as profile_module
from pet_harness.character.customization_service import CharacterCustomizationService
from pet_harness.character.personal import PersonalValidationError
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.runtime.provider_runtime import ProviderRuntime
from tests.conftest import FakeProvider

_SKILL_FIXTURES = {
    "joke_skill": {"trigger": "joke, funny", "behavior": "laugh", "xp_reward": "5"},
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


def _write_character(root: Path, character_id: str, skill_config: list[str]) -> None:
    assets_dir = root / "assets" / "webm" / "characters" / character_id
    (assets_dir / "motions").mkdir(parents=True)
    manifest = {
        "id": character_id, "name": character_id, "background_image": "",
        "motions_dir": f"assets/webm/characters/{character_id}/motions",
        "motions": {}, "idle_pool": [], "voice_id_env_key": "", "layout": {},
    }
    (assets_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    data_dir = root / "data" / "characters" / character_id
    data_dir.mkdir(parents=True)
    (data_dir / "profile.json").write_text(
        json.dumps({"persona_description": f"{character_id} default persona", "skill_config": skill_config}),
        encoding="utf-8",
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    _write_character(tmp_path, "Choppr", ["joke_skill"])
    _write_character(tmp_path, "miku", ["music_skill"])
    monkeypatch.setattr(profile_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    skills_dir = tmp_path / ".agentic" / "skills"
    skills_dir.mkdir(parents=True)
    for name, meta in _SKILL_FIXTURES.items():
        _write_skill(skills_dir, name, meta)

    registry = CharacterRegistry(
        assets_dir=str(tmp_path / "assets" / "webm" / "characters"),
        data_dir=str(tmp_path / "data" / "characters"),
    )
    router = CharacterRouter(
        registry=registry,
        agentic_root=str(tmp_path / ".agentic"),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )
    service = CharacterCustomizationService(registry=registry, router=router, project_root=tmp_path)
    return tmp_path, router, service


class TestSavePersona:
    def test_save_persona_for_active_character_refreshes_runtime(self, env):
        _tmp_path, router, service = env
        router.switch_character("miku")

        result = service.save_persona("miku", "custom miku persona")

        assert result["persona"] == "custom miku persona"
        assert router.get_active_character().effective_persona == "custom miku persona"

    def test_save_persona_for_inactive_character_preserves_active_runtime(self, env):
        _tmp_path, router, service = env
        router.switch_character("miku")

        service.save_persona("Choppr", "choppr new persona")

        assert router.get_active_character().character_id == "miku"
        choppr_view = service.get_customization("Choppr")
        assert choppr_view["persona"] == "choppr new persona"

    def test_invalid_persona_raises_and_leaves_previous_document_untouched(self, env):
        tmp_path, router, service = env
        router.switch_character("miku")
        service.save_persona("miku", "safe persona")

        with pytest.raises(PersonalValidationError):
            service.save_persona("miku", "see https://evil.example/leak")

        assert router.get_active_character().effective_persona == "safe persona"
        personal_path = tmp_path / "data" / "characters" / "miku" / "personal.json"
        assert json.loads(personal_path.read_text(encoding="utf-8"))["persona"] == "safe persona"

    def test_library_character_gets_and_saves_persona_in_character_data(self, env, monkeypatch):
        tmp_path, router, service = env
        monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
        monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
        monkeypatch.setattr(library_module, "UI_MUSIC_DIR", tmp_path / "ui" / "assets" / "music")
        character_dir = tmp_path / "assets" / "characters" / "char-angle"
        character_dir.mkdir(parents=True)
        (character_dir / "manifest.json").write_text(json.dumps({
            "id": "char-angle", "name": "Angle", "background_image": "",
            "motions_dir": "assets/characters/char-angle/motions", "motions": {},
            "idle_pool": [], "voice_id_env_key": "", "layout": {},
        }), encoding="utf-8")

        assert router.load_profile("char-angle")[0].allowed_skill_refs == ["youtube_music_playback", "bahamut_daily_news"]
        assert service.get_customization("char-angle")["character_id"] == "char-angle"
        assert service.save_persona("char-angle", "library persona")["persona"] == "library persona"
        assert json.loads((tmp_path / "data" / "characters" / "char-angle" / "personal.json").read_text(encoding="utf-8"))["persona"] == "library persona"


class TestLocalSkillCrud:
    def test_create_local_skill_is_routable_only_for_its_character(self, env):
        _tmp_path, router, service = env
        router.switch_character("miku")

        service.upsert_local_skill(
            "miku",
            {"skill_id": "local_cheer", "description": "cheer up", "triggers": ["加油"], "behavior": "laugh", "xp_reward": 1},
        )

        miku_engine = router.get_active_engine()
        assert "local_cheer" in {s.name for s in miku_engine.skills}

        router.switch_character("Choppr")
        choppr_engine = router.get_active_engine()
        assert "local_cheer" not in {s.name for s in choppr_engine.skills}

    def test_unsafe_local_skill_payload_is_rejected_without_partial_write(self, env):
        tmp_path, router, service = env
        router.switch_character("miku")

        with pytest.raises(PersonalValidationError):
            service.upsert_local_skill(
                "miku",
                {"skill_id": "bad_skill", "description": "x", "trigger": "y", "behavior": "../escape", "xp_reward": 1},
            )

        personal_path = tmp_path / "data" / "characters" / "miku" / "personal.json"
        assert not personal_path.exists()
        assert not (tmp_path / "data" / "characters" / "miku" / "skills" / "bad_skill.md").exists()

    def test_delete_local_skill_removes_it_after_refresh_and_leaves_other_character_alone(self, env):
        _tmp_path, router, service = env
        service.upsert_local_skill(
            "miku",
            {"skill_id": "shared_name", "description": "d", "trigger": "x", "behavior": "laugh", "xp_reward": 1},
        )
        service.upsert_local_skill(
            "Choppr",
            {"skill_id": "shared_name", "description": "d", "trigger": "x", "behavior": "laugh", "xp_reward": 1},
        )
        router.switch_character("miku")

        service.delete_local_skill("miku", "shared_name")

        assert "shared_name" not in {s.name for s in router.get_active_engine().skills}
        router.switch_character("Choppr")
        assert "shared_name" in {s.name for s in router.get_active_engine().skills}


class TestSkillOverride:
    def test_alias_only_routes_while_its_character_is_active(self, env):
        _tmp_path, router, service = env
        router.switch_character("miku")

        service.save_skill_override("miku", "music_skill", ["放歌"], 3)

        assert router.get_active_engine().router.match("幫我放歌") is not None
        router.switch_character("Choppr")
        assert router.get_active_engine().router.match("幫我放歌") is None

    def test_override_on_unauthorized_skill_raises(self, env):
        _tmp_path, router, service = env
        router.switch_character("miku")

        with pytest.raises(PersonalValidationError):
            service.save_skill_override("miku", "joke_skill", ["笑話"], 1)


class TestPreview:
    def test_preview_requires_active_character(self, env):
        _tmp_path, router, service = env
        router.switch_character("miku")

        with pytest.raises(ValueError):
            service.preview_skill_match("Choppr", "hello")

    def test_preview_reports_no_execution_side_effects(self, env):
        _tmp_path, router, service = env
        router.switch_character("miku")
        xp_before = router.get_active_engine().get_xp()

        diagnostics = service.preview_skill_match("miku", "play some bgm")

        assert diagnostics["matched"] is True
        assert diagnostics["skill_id"] == "music_skill"
        assert diagnostics["source"] == "builtin"
        assert router.get_active_engine().get_xp() == xp_before
