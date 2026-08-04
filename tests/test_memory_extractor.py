from pet_harness.memory.memory_extractor import LlmMemoryExtractor, WholeTurnMemoryExtractor


def test_whole_turn_extractor_is_fail_open_fallback():
    items = WholeTurnMemoryExtractor().extract("e1", "我明天要去台北", "好")
    assert items[0].memory_type == "episodic"
    assert items[0].source_event_id == "e1"


def test_llm_extractor_uses_structured_user_grounded_candidates_and_falls_back():
    extractor = LlmMemoryExtractor(lambda _user, _reply: '[{"memory_key":"food","memory_type":"semantic","text":"喜歡蘋果"}]')
    assert extractor.extract("e1", "我喜歡蘋果", "知道了")[0].memory_key == "使用者.喜好"
    assert LlmMemoryExtractor(lambda *_: "invalid").extract("e1", "我喜歡蘋果", "知道了")[0].memory_type == "semantic"
