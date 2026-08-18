from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.models.events import UserEvent
from pet_harness.memory.memory_models import MemoryItem, RetrievalResult, RetrievalTrace


def test_output_contract_requires_traditional_chinese_taiwan_usage(tmp_path):
    prompt = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {}).prompt

    assert "繁體中文（台灣用語）" in prompt


def test_memory_evidence_shows_attribute_without_retrieval_metadata(tmp_path):
    item = MemoryItem("m1", "miku", "default", "使用者.喜好.拉麵", "semantic", "我喜歡拉麵", "active", "e1", "2026-01-01T00:00:00+00:00")
    result = RetrievalResult([item], RetrievalTrace.empty("拉麵"))
    prompt = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {}, retrieval_result=result).prompt
    assert "[喜好] 我喜歡拉麵" in prompt
    assert "m1" not in prompt
    assert "score" not in prompt
