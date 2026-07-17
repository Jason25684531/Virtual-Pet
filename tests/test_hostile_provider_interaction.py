"""Deterministic Harness interaction smoke test:不可信 Provider 輸出不得中止
互動流程。涵蓋案例一(正常 object JSON)與案例二(hostile list-root JSON),
兩者都必須完成回覆、XP 更新與 event_log 寫入。全程使用 fake provider,不讀
真實 API key、不連網路、不啟動 PyQt GUI。"""

from __future__ import annotations

import pytest

from pet_harness.agent.provider_adapter import ProviderReply
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.provider import ProviderStatus, ProviderType

from tests.conftest import FakeProvider
from tests.test_harness_per_character import harness_env  # noqa: F401  (reused fixture)


class HostileProvider:
    """回傳合法 JSON 但 root 為 list 的不可信 Provider,用來驗證 parser fallback
    不會讓整輪互動拋出未捕捉例外。"""

    def generate_reply(self, event, matched_skill=None, prompt_text=None) -> ProviderReply:
        return ProviderReply(
            reply="raw fallback reply text",
            provider_status=ProviderStatus(provider_type=ProviderType.API, healthy=True, message="ok"),
            raw_text="[1, 2, 3]",
        )


def _build_engine(harness_env, provider) -> PetHarnessEngine:
    tmp_path, agentic_root = harness_env
    return PetHarnessEngine(
        provider,
        agentic_root=agentic_root,
        db_path=tmp_path / "state.db",
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json",
        character_id="Choppr",
    )


class TestNormalObjectJsonCompletesInteraction:
    def test_normal_provider_output_produces_valid_pet_event(self, harness_env):
        engine = _build_engine(harness_env, FakeProvider())
        before_xp = engine.store.get_user_progress()["xp_total"]

        event = engine.handle_event({"text": "hello there", "source": "test"})

        assert event.reply
        assert engine.store.get_user_progress()["xp_total"] > before_xp
        assert engine.store.recent_events(limit=1)


class TestHostileListRootCompletesInteraction:
    def test_hostile_provider_output_still_completes_the_interaction(self, harness_env):
        engine = _build_engine(harness_env, HostileProvider())
        before_xp = engine.store.get_user_progress()["xp_total"]
        before_events = len(engine.store.recent_events(limit=100))

        event = engine.handle_event({"text": "hello there", "source": "test"})

        assert event.reply
        assert event.metadata["agentic"]["fallback_used"] is True
        assert engine.store.get_user_progress()["xp_total"] > before_xp
        assert len(engine.store.recent_events(limit=100)) == before_events + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
