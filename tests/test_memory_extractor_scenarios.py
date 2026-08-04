from pet_harness.memory.memory_extractor import LlmMemoryExtractor, WholeTurnMemoryExtractor, is_valid_memory_key


def test_llm_extractor_accepts_fenced_and_unclosed_fenced_json_arrays():
    payload = '[{"memory_key":"使用者.最愛水果","memory_type":"semantic","text":"使用者最喜歡蘋果"}]'
    for raw in (f"```json\n{payload}\n```", f"```json\n{payload}"):
        items = LlmMemoryExtractor(lambda *_: raw).extract("e1", "我最喜歡蘋果", "知道了")
        assert [(item.memory_key, item.text) for item in items] == [("使用者.最愛水果", "使用者最喜歡蘋果")]


def test_memory_key_is_constrained_and_invalid_llm_key_falls_back():
    assert is_valid_memory_key("使用者.最愛水果")
    assert is_valid_memory_key("角色.承諾")
    assert not is_valid_memory_key("food")
    assert not is_valid_memory_key("使用者.喜歡.水果")
    item = LlmMemoryExtractor(lambda *_: '[{"memory_key":"food","memory_type":"semantic","text":"使用者最喜歡蘋果"}]').extract("e1", "我最喜歡蘋果", "知道了")[0]
    assert item.memory_key == "使用者.最愛水果"


def test_fallback_extracts_assistant_promises_but_not_greetings_tool_results_or_character_claims():
    extractor = WholeTurnMemoryExtractor()
    promise = extractor.extract("e1", "請明天提醒我", "我會在明天提醒你")
    assert [(item.memory_key, item.memory_type) for item in promise] == [("角色.承諾", "episodic")]
    assert extractor.extract("e2", "你好", "你好呀") == []
    assert extractor.extract("e3", "幫我查天氣", "工具結果：台北晴天") == []
    assert extractor.extract("e4", "你是誰", "我是初音未來，住在東京") == []


def test_fallback_keeps_multiple_user_preferences_under_one_allowed_key():
    items = WholeTurnMemoryExtractor().extract("e1", "我喜歡蘋果，也喜歡芒果", "知道了")
    assert len(items) == 1
    assert all(is_valid_memory_key(item.memory_key) for item in items)


def test_questions_and_conditional_offers_do_not_become_memory_items():
    extractor = WholeTurnMemoryExtractor()

    assert extractor.extract("e1", "我下周有什麼規劃嗎？", "你下周要看牙醫") == []
    assert LlmMemoryExtractor(lambda *_: '[{"memory_key":"角色.承諾","memory_type":"episodic","text":"如果需要幫忙請告訴我"}]').extract(
        "e2", "可以幫忙嗎？", "如果需要幫忙請告訴我"
    ) == []


def test_extractor_keeps_explicit_promises_but_drops_second_person_echoes():
    promise = LlmMemoryExtractor(lambda *_: '[{"memory_key":"角色.承諾","memory_type":"episodic","text":"下次我幫你查攻略"}]').extract(
        "e1", "幫我查攻略", "下次我幫你查攻略"
    )
    assert [(item.memory_key, item.text) for item in promise] == [("角色.承諾", "下次我幫你查攻略")]

    echo = LlmMemoryExtractor(lambda *_: '[{"memory_key":"使用者.喜好","memory_type":"semantic","text":"你喜歡蘋果"}]').extract(
        "e2", "我喜歡蘋果", "你喜歡蘋果"
    )
    assert [(item.memory_key, item.text) for item in echo] == [("使用者.喜好", "我喜歡蘋果")]


def test_memory_key_uses_only_the_allowed_traditional_attributes():
    assert not is_valid_memory_key("使用者.偏好")
    assert not is_valid_memory_key("角色.计划")
