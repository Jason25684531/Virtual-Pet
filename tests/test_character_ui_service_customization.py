"""任務 5.1:CharacterUiService 客製化 API 的 bridge 契約 — 角色身分、驗證錯誤、非執行預覽。

CharacterUiBridge 是薄 QObject 包裝(把這裡的回傳值/例外轉成 {"ok": ...} JSON),
需要真正的 QApplication/QObject parent 才能建構,現有專案測試慣例(見
test_character_ui_service.py)一律在 CharacterUiService 這一層驗證契約。
「取消草稿」是純前端行為(不呼叫 bridge),不需要 Python 測試。
"""

import json
from pathlib import Path

import pytest

import pet_harness.character.profile as profile_module
from pet_harness.character.exceptions import CharacterNotFoundError
from pet_harness.character.personal import PersonalValidationError
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.ui.character_ui_service import CharacterUiService

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
def service(tmp_path, monkeypatch):
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
    router = CharacterRouter(registry=registry, agentic_root=str(tmp_path / ".agentic"))
    return CharacterUiService(router=router, registry=registry), router


class TestCharacterIdentity:
    def test_get_customization_identifies_requested_character(self, service):
        ui_service, router = service
        router.switch_character("miku")

        result = ui_service.get_customization("miku")

        assert result["character_id"] == "miku"

    def test_saving_one_character_never_reports_or_mutates_the_other(self, service):
        ui_service, router = service
        router.switch_character("miku")

        result = ui_service.save_persona("miku", "miku only persona")

        assert result["character_id"] == "miku"
        choppr_view = ui_service.get_customization("Choppr")
        assert choppr_view["persona"] == "Choppr default persona"

    def test_unknown_character_raises(self, service):
        ui_service, _router = service
        with pytest.raises(CharacterNotFoundError):
            ui_service.get_customization("ghost")


class TestValidationErrors:
    def test_invalid_persona_raises_without_persisting(self, service):
        ui_service, router = service
        router.switch_character("miku")

        with pytest.raises(PersonalValidationError):
            ui_service.save_persona("miku", "token: sk-leak-me")

    def test_invalid_local_skill_raises_and_shows_no_data(self, service):
        ui_service, router = service
        router.switch_character("miku")

        with pytest.raises(PersonalValidationError):
            ui_service.upsert_local_skill(
                "miku",
                {"skill_id": "bad id with spaces", "description": "d", "trigger": "x", "behavior": "laugh", "xp_reward": 1},
            )
        assert ui_service.get_customization("miku")["local_skills"] == []

    def test_unauthorized_override_raises(self, service):
        ui_service, router = service
        router.switch_character("Choppr")

        with pytest.raises(PersonalValidationError):
            ui_service.save_skill_override("Choppr", "music_skill", ["音樂"], 1)


class TestNoExecutionPreviewContract:
    def test_preview_never_awards_xp_or_dispatches_events(self, service):
        ui_service, router = service
        router.switch_character("miku")
        xp_before = router.get_active_engine().get_xp()

        diagnostics = ui_service.preview_skill_match("miku", "play some bgm")

        assert diagnostics["matched"] is True
        assert router.get_active_engine().get_xp() == xp_before
        assert router.get_active_engine().store.recent_events(limit=1) == []

    def test_preview_for_non_active_character_raises(self, service):
        ui_service, router = service
        router.switch_character("miku")

        with pytest.raises(ValueError):
            ui_service.preview_skill_match("Choppr", "hello")
