import json
from pathlib import Path

import pytest

import pet_harness.character.profile as profile_module
from pet_harness.character.exceptions import CharacterNotFoundError, NoActiveCharacterError
from pet_harness.character.profile import CharacterProfile
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.engine.harness_engine import PetHarnessEngine

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
    motions: dict[str, str],
    voice_id_env_key: str,
) -> None:
    assets_dir = root / "assets" / "webm" / "characters" / character_id
    (assets_dir / "motions").mkdir(parents=True)
    manifest = {
        "id": character_id,
        "name": character_id,
        "background_image": "",
        "motions_dir": f"assets/webm/characters/{character_id}/motions",
        "motions": motions,
        "idle_pool": [],
        "voice_id_env_key": voice_id_env_key,
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
def router(tmp_path, monkeypatch):
    """建立 tmp 根目錄：Choppr / miku 兩個角色資料 + 共用 agentic_root skills，回傳 CharacterRouter。

    CharacterProfile.sqlite_path 是相對路徑（"data/characters/{id}/state.db"），
    不吃 _PROJECT_ROOT，而是相對於當前工作目錄解析——這裡連同 chdir 到 tmp_path，
    避免 SQLiteStore 把測試資料寫進真實專案的 data/ 目錄。
    """
    _write_character(
        tmp_path,
        "Choppr",
        ["joke_skill", "mood_skill"],
        motions={"idle": "motions/idle.webm", "laugh": "motions/laugh.webm"},
        voice_id_env_key="CHOPPR_VOICE_ID",
    )
    _write_character(
        tmp_path,
        "miku",
        ["music_skill"],
        motions={"idle": "motions/idle.webm", "sing": "motions/sing.webm"},
        voice_id_env_key="MIKU_VOICE_ID",
    )
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
    return CharacterRouter(registry=registry, agentic_root=str(agentic_root))


def test_initial_state(router):
    assert router.get_active_character() is None
    assert router.get_active_engine() is None


def test_switch_character_choppr(router):
    profile = router.switch_character("Choppr")
    assert profile.character_id == "Choppr"
    assert router.get_active_character().character_id == "Choppr"
    engine = router.get_active_engine()
    assert isinstance(engine, PetHarnessEngine)
    assert engine._character_id == "Choppr"


def test_switch_character_updates_motions(router):
    router.switch_character("Choppr")
    motions = router.get_active_motions()
    assert "idle" in motions
    assert motions == CharacterProfile.load("Choppr").motions


def test_switch_character_updates_voice_id(router):
    router.switch_character("Choppr")
    assert router.get_voice_id_env_key() == CharacterProfile.load("Choppr").voice_id_env_key


def test_switch_not_found_preserves_active(router):
    router.switch_character("Choppr")
    with pytest.raises(CharacterNotFoundError):
        router.switch_character("ghost")
    assert router.get_active_character().character_id == "Choppr"


def test_consecutive_switch(router):
    router.switch_character("Choppr")
    router.switch_character("miku")
    assert router.get_active_character().character_id == "miku"


def test_dispatch_event_delegates_to_engine(router, monkeypatch):
    router.switch_character("Choppr")
    engine = router.get_active_engine()
    calls = []

    def fake_handle_event(event):
        calls.append(event)
        return "sentinel-result"

    monkeypatch.setattr(engine, "handle_event", fake_handle_event)
    result = router.dispatch_event({"text": "hi", "source": "test"})
    assert result == "sentinel-result"
    assert calls == [{"text": "hi", "source": "test"}]


def test_dispatch_no_active_raises(router):
    with pytest.raises(NoActiveCharacterError):
        router.dispatch_event({"text": "hi"})


def test_get_active_motions_no_active(router):
    assert router.get_active_motions() == {}
