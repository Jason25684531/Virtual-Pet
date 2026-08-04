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

    assert "## Retrieval Evidence" in engine.last_prompt
    assert "我喜歡的水果是蘋果" in engine.last_prompt


def test_persona_instruction_names_the_actual_evidence_section(harness_env):
    """人設優先指示所指涉的區塊名稱必須是實際存在的 Retrieval Evidence。"""
    tmp_path, agentic_root = harness_env
    engine = PetHarnessEngine(
        FakeProvider(),
        agentic_root=agentic_root,
        db_path=tmp_path / "state.db",
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json",
        character_id="Choppr",
    )

    engine.handle_event({"text": "你好", "source": "test"})

    assert "Relevant Memories" not in engine.last_prompt
    assert "the persona always wins" in engine.last_prompt


def test_prompt_instructs_the_model_to_use_evidence_and_keeps_persona_priority(harness_env):
    """證據使用指示與人設優先指示必須並存:前者管無衝突時要不要用,後者管衝突時誰贏。

    依據 2026-07-29 A/B/C 實測:缺少使用指示時,同一模型即使證據在 prompt 內也拒絕作答。
    """
    tmp_path, agentic_root = harness_env
    engine = PetHarnessEngine(
        FakeProvider(),
        agentic_root=agentic_root,
        db_path=tmp_path / "state.db",
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json",
        character_id="Choppr",
    )

    engine.handle_event({"text": "你好", "source": "test"})
    prompt = engine.last_prompt

    assert "Conversation History and Retrieval Evidence are factual records of what the user told you" in prompt
    assert "answer from those sections" in prompt
    assert "cannot access" in prompt
    assert "the persona always wins" in prompt


def test_prompt_keeps_user_facts_separate_from_echoes_own_state(harness_env):
    """使用者記憶不得用來回答 ECHOES 自己的行程或狀態。"""
    tmp_path, agentic_root = harness_env
    engine = PetHarnessEngine(
        FakeProvider(),
        agentic_root=agentic_root,
        db_path=tmp_path / "state.db",
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json",
        character_id="Choppr",
    )

    engine.handle_event({"text": "我要去福岡七天六夜", "source": "test"})
    engine.handle_event({"text": "那你下周要幹嘛？", "source": "test"})

    assert "Do not use user facts to answer questions about ECHOES's own plans" in engine.last_prompt


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
