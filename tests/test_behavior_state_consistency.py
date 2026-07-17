"""BehaviorManager 持久化語意回歸測試:behavior_state 只代表 persisted fallback
behavior;skill behavior 與 action motion 是 transient presentation,絕不寫入
persisted state。涵蓋四個轉移:skill 命中、action motion、fallback 寫回、
unknown state 自我修復。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pet_harness.behavior.behavior_manager import BehaviorManager
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.skill import Skill
from pet_harness.storage.sqlite_store import SQLiteStore

from tests.conftest import FakeProvider
from tests.test_harness_per_character import harness_env  # noqa: F401  (reused fixture)


def _make_manager(tmp_path: Path) -> BehaviorManager:
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    behavior_map_path = tmp_path / "behavior_map.json"
    behavior_map_path.write_text(
        json.dumps({"behaviors": {"idle": {"webm_key": "idle"}, "dance": {"webm_key": "dance_webm"}}}),
        encoding="utf-8",
    )
    return BehaviorManager(store, behavior_map_path)


def _skill(name: str, behavior: str) -> Skill:
    return Skill(name=name, description="d", triggers=["t"], behavior=behavior, xp_reward=1)


class TestSkillHitDoesNotPersist:
    def test_skill_behavior_does_not_overwrite_persisted_fallback(self, tmp_path):
        manager = _make_manager(tmp_path)
        assert manager.store.get_behavior_state() == "idle"

        event = manager.resolve(_skill("joke_skill", "dance"))

        assert event.behavior_id == "dance"
        assert manager.store.get_behavior_state() == "idle"


class TestActionMotionDoesNotPersist:
    def test_action_motion_does_not_overwrite_persisted_fallback(self, tmp_path):
        """既有 resolution path 提供的一次性 action motion key 不要求存在於
        behavior_map,且絕不得寫入 persisted fallback state(此前為現行 bug)。"""
        manager = _make_manager(tmp_path)
        assert manager.store.get_behavior_state() == "idle"

        event = manager.resolve(None, action_motion_key="wave_hello")

        assert event.behavior_id == "wave_hello"
        assert event.webm_key == "wave_hello"
        assert manager.store.get_behavior_state() == "idle"


class TestFallbackPersists:
    def test_fallback_path_persists_resolved_behavior(self, tmp_path):
        manager = _make_manager(tmp_path)
        manager.store.set_behavior_state("dance")

        event = manager.resolve(None, action_motion_key=None)

        assert event.behavior_id == "dance"
        assert manager.store.get_behavior_state() == "dance"


class TestUnknownStateSelfHeals:
    def test_unknown_persisted_state_self_heals_to_idle(self, tmp_path):
        manager = _make_manager(tmp_path)
        manager.store.set_behavior_state("ghost_behavior")

        event = manager.resolve(None, action_motion_key=None)

        assert event.behavior_id == "idle"
        assert manager.store.get_behavior_state() == "idle"


class TestSkillThenFallbackSequence:
    def test_skill_interaction_followed_by_fallback_uses_persisted_idle(self, tmp_path):
        manager = _make_manager(tmp_path)

        manager.resolve(_skill("joke_skill", "dance"))
        second = manager.resolve(None, action_motion_key=None)

        assert second.behavior_id == "idle"
        assert manager.store.get_behavior_state() == "idle"


class TestPetEventMirrorsBehaviorEvent:
    """UI 使用的 behavior 必須與 domain 決定結果一致:PetEvent.behavior_id/webm_key
    直接來自本輪 BehaviorEvent,以 skill 與 fallback 兩個實際互動路徑驗證。"""

    def _build_engine(self, harness_env) -> PetHarnessEngine:
        tmp_path, agentic_root = harness_env
        return PetHarnessEngine(
            FakeProvider(),
            agentic_root=agentic_root,
            db_path=tmp_path / "state.db",
            snapshot_path=tmp_path / "debug" / "latest_pet_event.json",
            character_id="Choppr",
        )

    def test_skill_path_pet_event_matches_behavior_event(self, harness_env):
        engine = self._build_engine(harness_env)

        event = engine.handle_event({"text": "tell me a joke", "source": "test"})

        assert event.matched_skill == "joke_skill"
        assert event.behavior_id == event.metadata["behavior"]["behavior_id"]
        assert event.webm_key == event.metadata["behavior"]["webm_key"]

    def test_fallback_path_pet_event_matches_behavior_event(self, harness_env):
        engine = self._build_engine(harness_env)

        event = engine.handle_event({"text": "totally unmatched text xyz", "source": "test"})

        assert event.matched_skill is None
        assert event.behavior_id == event.metadata["behavior"]["behavior_id"]
        assert event.webm_key == event.metadata["behavior"]["webm_key"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
