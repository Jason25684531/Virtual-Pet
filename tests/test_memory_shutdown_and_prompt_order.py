"""關閉後索引不得炸 + User Text 必須是 prompt 最後一個內容區塊。"""
from pathlib import Path

from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_models import MemoryItem
from pet_harness.models.events import UserEvent


class _StubEncoder:
    """避免測試依賴 fastembed 下載真模型;本測試只驗生命週期,不驗檢索品質。"""

    def embed(self, texts):
        return iter([[0.1] * 384 for _ in texts])


def test_index_after_shutdown_is_noop_not_crash(tmp_path):
    store = HybridQdrantMemoryStore(character_id="c1", path=tmp_path / "qdrant", dense_encoder=_StubEncoder())
    assert store.status().state == "ready", store.status().reason
    mid = "11111111-2222-3333-4444-555555555555"
    item = MemoryItem(mid, "c1", "default", "使用者.喜好.蘋果", "semantic", "使用者喜歡蘋果", "active", "e1", "2026-01-01")
    assert store.index([item]) == [mid]

    store.shutdown()
    # 背景 thread 在關閉後才跑到 index()/recall():回空,不得拋 QdrantLocal is closed
    assert store.index([item]) == []
    assert store.recall("蘋果") == []


def test_user_text_is_the_last_content_block():
    prompt = PromptBuilder(Path(".agentic")).build(
        UserEvent(text="我喜歡蘋果"), [], {},
        conversation_history=[{"input_payload": {"text": "說個笑話"},
                               "output_payload": {"reply": "先問你喜歡什麼樣的笑話呀？"}}],
    ).prompt
    # User Text 必須排在 response rules 之後,否則 12B 模型會照抄上一輪回覆
    assert prompt.index("## User Text") > prompt.index("## Global Response Rules")
    assert prompt.index("## User Text") > prompt.index("## Conversation History")
    assert "我喜歡蘋果" in prompt
