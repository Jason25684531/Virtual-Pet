"""任務 2.4:Choppr/miku 往返切換整合測試 — engine、SQLite、skills、Provider、snapshot 全程一致。"""

import json
from pathlib import Path

import pytest

import pet_harness.character.profile as profile_module
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.runtime.provider_runtime import ProviderRuntime
from pet_harness.storage.sqlite_store import SQLiteStore
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


def _write_character(root: Path, character_id: str, skill_config: list[str], voice_key: str) -> None:
    assets_dir = root / "assets" / "webm" / "characters" / character_id
    (assets_dir / "motions").mkdir(parents=True)
    manifest = {
        "id": character_id,
        "name": character_id,
        "background_image": "",
        "motions_dir": f"assets/webm/characters/{character_id}/motions",
        "motions": {"idle": "motions/idle.webm"},
        "idle_pool": [],
        "voice_id_env_key": voice_key,
        "layout": {},
    }
    (assets_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    data_dir = root / "data" / "characters" / character_id
    data_dir.mkdir(parents=True)
    (data_dir / "profile.json").write_text(
        json.dumps({"persona_description": f"{character_id} persona", "skill_config": skill_config}),
        encoding="utf-8",
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    _write_character(tmp_path, "Choppr", ["joke_skill"], "CHOPPR_VOICE_ID")
    _write_character(tmp_path, "miku", ["music_skill"], "MIKU_VOICE_ID")
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
    provider = FakeProvider()
    runtime = ProviderRuntime(provider=provider)
    router = CharacterRouter(
        registry=registry,
        agentic_root=str(tmp_path / ".agentic"),
        provider_runtime=runtime,
    )
    return tmp_path, router, runtime, provider


def test_round_trip_switch_keeps_everything_consistent(env):
    tmp_path, router, runtime, provider = env
    status_before = runtime.get_status()

    for expected_id, expected_skills, expected_voice in [
        ("Choppr", {"joke_skill"}, "CHOPPR_VOICE_ID"),
        ("miku", {"music_skill"}, "MIKU_VOICE_ID"),
        ("Choppr", {"joke_skill"}, "CHOPPR_VOICE_ID"),
    ]:
        router.switch_character(expected_id)
        snapshot = router.get_active_snapshot()
        engine = router.get_active_engine()

        # snapshot / engine / SQLite / skills / voice 指向同一角色
        assert snapshot.character_id == expected_id
        assert engine._character_id == expected_id
        assert Path(engine.store.db_path).resolve() == (
            tmp_path / "data" / "characters" / expected_id / "state.db"
        ).resolve()
        assert {skill.name for skill in engine.skills} == expected_skills
        assert set(snapshot.skill_refs) == expected_skills
        assert snapshot.voice_id_env_key == expected_voice

        # Provider 是同一個共用 runtime,狀態不因切換而改變
        assert engine.provider is runtime
        assert runtime.get_status().to_dict() == status_before.to_dict()


def test_switch_never_writes_provider_config_to_character_db(env):
    tmp_path, router, runtime, provider = env
    router.switch_character("Choppr")
    router.dispatch_event({"text": "tell me a joke", "source": "test"})
    router.switch_character("miku")
    router.dispatch_event({"text": "play music", "source": "test"})

    for character_id in ("Choppr", "miku"):
        store = SQLiteStore(tmp_path / "data" / "characters" / character_id / "state.db")
        assert store.get_setting("provider_config") is None


def test_dispatch_uses_shared_provider_after_switch(env):
    _tmp_path, router, _runtime, provider = env
    router.switch_character("Choppr")
    router.dispatch_event({"text": "hello choppr", "source": "test"})
    router.switch_character("miku")
    event = router.dispatch_event({"text": "hello miku", "source": "test"})

    assert provider.calls == ["hello choppr", "hello miku"]
    assert event.reply.startswith("[fake]")
