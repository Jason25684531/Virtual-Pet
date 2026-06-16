import json
import subprocess
import sys
from pathlib import Path

import pytest

from pet_harness.agent.langchain_adapter import LangChainAdapter
from pet_harness.agent.mock_provider import MockProvider
from pet_harness.asset.asset_contract import AssetRequest, AssetResponse
from pet_harness.behavior.behavior_manager import BehaviorManager
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.events import PetEvent, UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderStatus, ProviderType
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.xp.reward_manager import RewardManager
from pet_harness.xp.xp_manager import XPManager


def test_user_event_and_pet_event_serialize_with_stable_fields():
    user_event = UserEvent(text="play some bgm", source="pytest")
    user_payload = user_event.to_dict()

    assert user_payload["event_type"] == "text"
    assert user_payload["text"] == "play some bgm"
    assert user_payload["source"] == "pytest"
    assert user_payload["event_id"]

    pet_event = PetEvent(
        source_event_id=user_event.event_id,
        reply="BGM request accepted.",
        matched_skill="music_bgm",
        behavior_id="music_idle",
        webm_key="music_idle",
        xp_delta=8,
        provider_status=ProviderStatus(provider_type=ProviderType.MOCK).to_dict(),
        saved_to_db=True,
    )

    pet_payload = pet_event.to_dict()
    assert pet_payload["reply"] == "BGM request accepted."
    assert pet_payload["matched_skill"] == "music_bgm"
    assert pet_payload["behavior_id"] == "music_idle"
    assert pet_payload["webm_key"] == "music_idle"
    assert pet_payload["xp_delta"] == 8
    assert pet_payload["saved_to_db"] is True


def test_default_skills_load_and_invalid_skill_is_skipped(tmp_path, caplog):
    skill_dir = tmp_path / ".agentic" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "music_bgm.md").write_text(
        "\n".join(
            [
                "name: music_bgm",
                "description: Play background music.",
                "trigger: music, bgm, song",
                "behavior: music_idle",
                "xp_reward: 8",
                "required_tool: music_search",
                "unlock_reward: bgm_badge",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "broken.md").write_text("description: missing name", encoding="utf-8")

    skills = SkillLoader(skill_dir).load_skills()

    assert [skill.name for skill in skills] == ["music_bgm"]
    assert skills[0].triggers == ["music", "bgm", "song"]
    assert skills[0].xp_reward == 8
    assert "Skipping invalid skill" in caplog.text


def test_default_repository_skills_are_loadable():
    skills = SkillLoader(Path(".agentic") / "skills").load_skills()
    names = {skill.name for skill in skills}

    assert {"music_bgm", "game_news", "break_reminder", "gacha_fortune", "system_monitor"} <= names


def test_skill_router_matches_week_one_triggers():
    skills = SkillLoader(Path(".agentic") / "skills").load_skills()
    router = SkillRouter(skills)

    assert router.match("please play some bgm").name == "music_bgm"
    assert router.match("any game news today?").name == "game_news"
    assert router.match("remind me to take a break").name == "break_reminder"
    assert router.match("just chatting") is None


def test_sqlite_store_initializes_and_persists_core_state(tmp_path):
    db_path = tmp_path / "pet_state.db"
    store = SQLiteStore(db_path)
    store.initialize()

    skills = SkillLoader(Path(".agentic") / "skills").load_skills()
    store.sync_skills(skills)
    store.add_user_xp(5)
    store.add_skill_xp("music_bgm", 8)
    store.set_behavior_state("idle")
    store.set_provider_status(ProviderStatus(provider_type=ProviderType.MOCK, healthy=True))
    store.log_event({"text": "hi"}, {"reply": "hello"})

    reopened = SQLiteStore(db_path)
    reopened.initialize()

    assert reopened.get_user_progress()["xp_total"] == 5
    assert any(skill["name"] == "music_bgm" for skill in reopened.list_skills())
    assert reopened.get_skill_progress("music_bgm")["xp_total"] == 8
    assert reopened.get_behavior_state() == "idle"
    assert reopened.get_provider_status()["provider_type"] == "mock"
    assert reopened.recent_events(limit=1)[0]["output_payload"]["reply"] == "hello"


def test_xp_and_reward_managers_persist_threshold_unlocks_once(tmp_path):
    store = SQLiteStore(tmp_path / "pet_state.db")
    store.initialize()
    skills = SkillLoader(Path(".agentic") / "skills").load_skills()
    music_skill = next(skill for skill in skills if skill.name == "music_bgm")

    xp_manager = XPManager(store, chat_xp=2)
    delta = xp_manager.award_for_event(music_skill)

    assert delta == music_skill.xp_reward
    assert store.get_user_progress()["xp_total"] == music_skill.xp_reward

    reward_manager = RewardManager(store, Path(".agentic") / "rewards" / "reward_rules.json")
    first_unlocks = reward_manager.check_unlocks(store.get_user_progress()["xp_total"])
    second_unlocks = reward_manager.check_unlocks(store.get_user_progress()["xp_total"])

    assert first_unlocks
    assert second_unlocks == []
    assert store.list_inventory()
    assert store.list_reward_unlocks()


def test_behavior_manager_resolves_skill_and_safe_fallback(tmp_path):
    store = SQLiteStore(tmp_path / "pet_state.db")
    store.initialize()
    manager = BehaviorManager(store, Path(".agentic") / "behavior" / "behavior_map.json")
    skills = SkillLoader(Path(".agentic") / "skills").load_skills()
    music_skill = next(skill for skill in skills if skill.name == "music_bgm")

    behavior = manager.resolve(music_skill)
    assert behavior.behavior_id == "music_idle"
    assert behavior.webm_key == "music_idle"

    fallback = manager.resolve(None)
    assert fallback.behavior_id == "idle"
    assert fallback.webm_key == "idle"

    music_skill.behavior = "missing_behavior"
    missing = manager.resolve(music_skill)
    assert missing.behavior_id == "idle"
    assert missing.webm_key == "idle"


def test_mock_provider_and_langchain_placeholder_need_no_network():
    provider = MockProvider()
    result = provider.generate_reply(UserEvent(text="hello"), matched_skill=None)

    assert result.reply
    assert result.provider_status.provider_type is ProviderType.MOCK
    assert result.provider_status.healthy is True

    adapter = LangChainAdapter(provider)
    assert adapter.generate(UserEvent(text="hello"), matched_skill=None).reply == result.reply


def test_asset_contract_serializes_without_comfyui_client():
    request = AssetRequest(asset_type="webm", prompt_params={"style": "cozy"}, source_event_id="evt-1")
    response = AssetResponse(
        request_id=request.request_id,
        status="completed",
        asset_id="asset-1",
        file_path="assets/webm/generated/asset-1.webm",
        webm_key="reward_idle",
    )

    assert request.to_dict()["asset_type"] == "webm"
    assert response.to_dict()["status"] == "completed"


def test_harness_engine_handles_dict_event_and_writes_snapshot(tmp_path):
    db_path = tmp_path / "pet_state.db"
    snapshot_path = tmp_path / "debug" / "events" / "latest_pet_event.json"
    engine = PetHarnessEngine(
        agentic_root=Path(".agentic"),
        db_path=db_path,
        snapshot_path=snapshot_path,
    )

    event = engine.handle_event({"text": "please play some bgm", "source": "pytest"})

    assert event.matched_skill == "music_bgm"
    assert event.behavior_id == "music_idle"
    assert event.webm_key == "music_idle"
    assert event.xp_delta > 0
    assert event.saved_to_db is True
    assert snapshot_path.exists()
    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["matched_skill"] == "music_bgm"

    store = SQLiteStore(db_path)
    store.initialize()
    assert store.recent_events(limit=1)


def test_debug_cli_smoke_text_and_status(tmp_path):
    db_path = tmp_path / "pet_state.db"
    snapshot_path = tmp_path / "latest_pet_event.json"

    text_run = subprocess.run(
        [
            sys.executable,
            "scripts/debug_harness.py",
            "--text",
            "any game news?",
            "--db-path",
            str(db_path),
            "--snapshot-path",
            str(snapshot_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(text_run.stdout)
    assert payload["matched_skill"] == "game_news"
    assert snapshot_path.exists()

    status_run = subprocess.run(
        [
            sys.executable,
            "scripts/debug_harness.py",
            "--debug-status",
            "--db-path",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    status = json.loads(status_run.stdout)
    assert status["provider_status"]["provider_type"] == "mock"
    assert status["skill_count"] >= 5

