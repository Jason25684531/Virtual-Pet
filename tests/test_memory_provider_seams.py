from types import SimpleNamespace

from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.memory.memory_extractor import LlmMemoryExtractor
from pet_harness.memory.memory_models import RetrievalRequest


class _FencedProvider:
    def generate_reply(self, event, *, prompt_text):
        if event.source == "memory_extractor":
            return SimpleNamespace(raw_text='```json\n[{"memory_key":"使用者.最愛水果","memory_type":"semantic","text":"使用者最喜歡蘋果"}]', reply="")
        return SimpleNamespace(raw_text="```text\n使用者最喜歡什麼水果\n```", reply="")


def test_engine_provider_seams_handle_fenced_memory_json_and_rewrite_query():
    engine = PetHarnessEngine.__new__(PetHarnessEngine)
    engine.provider = _FencedProvider()

    items = LlmMemoryExtractor(engine._extract_memory_json).extract("e1", "我最喜歡蘋果", "知道了")
    assert [(item.memory_key, item.text) for item in items] == [("使用者.最愛水果", "使用者最喜歡蘋果")]
    assert engine._rewrite_query(RetrievalRequest("miku", "那我呢", "你最喜歡什麼水果"), timeout=1.25) == "使用者最喜歡什麼水果"
