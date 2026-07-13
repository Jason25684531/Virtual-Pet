import json
from pathlib import Path

import pytest

import pet_harness.character.profile as profile_module
from pet_harness.engine.harness_engine import PetHarnessEngine
from tests.conftest import FakeProvider

_SKILL_FIXTURES = {
    "joke_skill": {"trigger": "joke, funny", "behavior": "laugh", "xp_reward": "5"},
    "mood_skill": {"trigger": "mood, feeling", "behavior": "idle", "xp_reward": "3"},
    "report_skill": {"trigger": "report, news", "behavior": "report_news", "xp_reward": "6"},
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
        "id": character_id,
        "name": character_id,
        "background_image": "",
        "motions_dir": f"assets/webm/characters/{character_id}/motions",
        "motions": {},
        "idle_pool": [],
        "voice_id_env_key": "",
        "layout": {},
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
def harness_env(tmp_path, monkeypatch):
    """建立 tmp 根目錄：Choppr / miku 兩個角色資料 + 共用 agentic_root skills。

    CharacterProfile.sqlite_path 是相對路徑（"data/characters/{id}/state.db"），
    不吃 _PROJECT_ROOT，而是相對於當前工作目錄解析——這裡連同 chdir 到 tmp_path，
    避免 SQLiteStore 把測試資料寫進真實專案的 data/ 目錄。
    """
    _write_character(tmp_path, "Choppr", ["joke_skill", "mood_skill"])
    _write_character(tmp_path, "miku", ["report_skill", "music_skill"])
    monkeypatch.setattr(profile_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    agentic_root = tmp_path / ".agentic"
    skills_dir = agentic_root / "skills"
    skills_dir.mkdir(parents=True)
    for name, meta in _SKILL_FIXTURES.items():
        _write_skill(skills_dir, name, meta)

    return tmp_path, agentic_root


def _build_engine(agentic_root: Path, tmp_path: Path, character_id: str | None, db_path: Path | None = None):
    return PetHarnessEngine(
        agentic_root=agentic_root,
        db_path=db_path or (tmp_path / "legacy_pet_state.db"),
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json",
        provider=FakeProvider(),
        character_id=character_id,
    )


class TestCharacterIdLoading:
    def test_engine_loads_correct_profile(self, harness_env):
        tmp_path, agentic_root = harness_env
        engine = _build_engine(agentic_root, tmp_path, character_id="Choppr")
        assert engine._character_id == "Choppr"


class TestSqlitePathIsolation:
    def test_sqlite_path_isolation(self, harness_env):
        tmp_path, agentic_root = harness_env
        choppr = _build_engine(agentic_root, tmp_path, character_id="Choppr")
        miku = _build_engine(agentic_root, tmp_path, character_id="miku")

        assert choppr.store.db_path != miku.store.db_path
        assert choppr.store.db_path.resolve() == (
            tmp_path / "data" / "characters" / "Choppr" / "state.db"
        ).resolve()
        assert miku.store.db_path.resolve() == (
            tmp_path / "data" / "characters" / "miku" / "state.db"
        ).resolve()


class TestSkillRouterIsolation:
    def test_skill_router_isolation(self, harness_env):
        tmp_path, agentic_root = harness_env
        choppr = _build_engine(agentic_root, tmp_path, character_id="Choppr")
        miku = _build_engine(agentic_root, tmp_path, character_id="miku")

        choppr_names = {skill.name for skill in choppr.skills}
        miku_names = {skill.name for skill in miku.skills}

        assert choppr_names == {"joke_skill", "mood_skill"}
        assert miku_names == {"report_skill", "music_skill"}
        assert "report_skill" not in choppr_names
        assert "joke_skill" not in miku_names

    def test_skill_config_unknown_name_warns_not_raises(self, harness_env, tmp_path, monkeypatch):
        _tmp, agentic_root = harness_env
        data_dir = tmp_path / "data" / "characters" / "Choppr"
        profile = json.loads((data_dir / "profile.json").read_text(encoding="utf-8"))
        profile["skill_config"] = ["joke_skill", "ghost_skill"]
        (data_dir / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        engine = _build_engine(agentic_root, tmp_path, character_id="Choppr")

        assert {skill.name for skill in engine.skills} == {"joke_skill"}

    def test_disabled_skill_overlay_blocks_text_routing_and_reenable_restores_it(self, harness_env):
        tmp_path, agentic_root = harness_env
        engine = _build_engine(agentic_root, tmp_path, character_id="Choppr")

        engine.store.set_setting("character_skill_enabled", {"joke_skill": False, "mood_skill": True})
        engine.skills = engine.filter_skills_for_character(engine.skills)
        engine.router = engine.router.__class__(engine.skills)
        disabled_event = engine.handle_event({"text": "tell me a joke", "source": "test"})

        assert disabled_event.matched_skill is None
        engine.store.set_setting("character_skill_enabled", {"joke_skill": True, "mood_skill": True})
        restored = _build_engine(agentic_root, tmp_path, character_id="Choppr")

        assert {skill.name for skill in restored.skills} == {"joke_skill", "mood_skill"}
        assert restored.handle_event({"text": "tell me a joke", "source": "test"}).matched_skill == "joke_skill"

    def test_skill_behavior_takes_priority_over_action_tag(self, harness_env):
        tmp_path, agentic_root = harness_env
        engine = _build_engine(agentic_root, tmp_path, character_id="Choppr")
        skill = next(skill for skill in engine.skills if skill.name == "joke_skill")

        event = engine.behavior_manager.resolve(skill, action_motion_key="angry")

        assert event.reason == "skill"
        assert event.webm_key != "angry"


class TestXpIsolation:
    def test_xp_isolation(self, harness_env):
        tmp_path, agentic_root = harness_env
        choppr = _build_engine(agentic_root, tmp_path, character_id="Choppr")
        miku = _build_engine(agentic_root, tmp_path, character_id="miku")

        choppr.handle_event({"text": "tell me a joke", "source": "test"})

        assert choppr.store.get_user_progress()["xp_total"] > 0
        assert miku.store.get_user_progress()["xp_total"] == 0


class TestXpLevelConvenienceMethods:
    def test_new_character_initial_values(self, harness_env):
        tmp_path, agentic_root = harness_env
        choppr = _build_engine(agentic_root, tmp_path, character_id="Choppr")

        assert choppr.get_xp() == 0
        assert choppr.get_level() == 1

    def test_level_crosses_threshold(self, harness_env):
        tmp_path, agentic_root = harness_env
        choppr = _build_engine(agentic_root, tmp_path, character_id="Choppr")

        choppr.store.add_user_xp(150)

        assert choppr.get_xp() == 150
        assert choppr.get_level() == 2

    def test_per_character_isolation(self, harness_env):
        tmp_path, agentic_root = harness_env
        choppr = _build_engine(agentic_root, tmp_path, character_id="Choppr")
        miku = _build_engine(agentic_root, tmp_path, character_id="miku")

        choppr.store.add_user_xp(120)

        assert choppr.get_xp() == 120
        assert miku.get_xp() == 0


class TestLegacyCompatibility:
    def test_no_character_id_keeps_legacy_behavior(self, harness_env):
        tmp_path, agentic_root = harness_env
        legacy_db = tmp_path / "legacy_pet_state.db"
        engine = _build_engine(agentic_root, tmp_path, character_id=None, db_path=legacy_db)

        assert engine._character_id is None
        assert str(engine.store.db_path) == str(legacy_db)
        assert {skill.name for skill in engine.skills} == set(_SKILL_FIXTURES.keys())
