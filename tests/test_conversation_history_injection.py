"""蘋果測試:短期記憶(近期對話)注入 prompt,讓 LLM 能回答上一輪提到的事實。"""

from __future__ import annotations

from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.memory.base_memory_store import MemoryHit, NullMemoryStore

from tests.test_harness_per_character import harness_env  # noqa: F401  (reused fixture)
from tests.conftest import FakeProvider


def test_second_turn_prompt_includes_first_turn_conversation(harness_env):
    tmp_path, agentic_root = harness_env
    engine = PetHarnessEngine(
        FakeProvider(),
        agentic_root=agentic_root,
        db_path=tmp_path / "state.db",
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json",
        character_id="Choppr",
    )

    engine.handle_event({"text": "我喜歡的水果是蘋果", "source": "test"})
    engine.handle_event({"text": "你說看看我喜歡甚麼水果?", "source": "test"})

    assert "我喜歡的水果是蘋果" in engine.last_prompt
    assert "## Conversation History" in engine.last_prompt


def test_memory_recall_hits_are_injected_into_prompt(harness_env):
    tmp_path, agentic_root = harness_env

    class RecallingMemoryStore(NullMemoryStore):
        def recall(self, query: str, top_k: int = 3):
            return [MemoryHit("evt-old", "我喜歡的水果是蘋果\n蘋果，不錯呢。", 0.9)]

    engine = PetHarnessEngine(
        FakeProvider(),
        agentic_root=agentic_root,
        db_path=tmp_path / "state.db",
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json",
        character_id="Choppr",
        memory_store=RecallingMemoryStore(),
    )

    engine.handle_event({"text": "你說看看我喜歡甚麼水果?", "source": "test"})

    assert "## Relevant Memories" in engine.last_prompt
    assert "我喜歡的水果是蘋果" in engine.last_prompt


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
