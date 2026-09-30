from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.models.events import UserEvent
from pet_harness.memory.memory_models import MemoryItem, RetrievalResult, RetrievalTrace


def test_global_response_rules_are_before_output_contract(tmp_path):
    (tmp_path / "response_rules.md").write_text(
        "Rules override persona style.\nKeep replies short.", encoding="utf-8"
    )

    result = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {})

    assert "## Global Response Rules\nRules override persona style." in result.prompt
    assert result.prompt.index("## Global Response Rules") < result.prompt.index("## Output Contract")


def test_global_response_rules_are_included_without_persona(tmp_path):
    (tmp_path / "response_rules.md").write_text("Global rule", encoding="utf-8")

    prompt = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {}).prompt

    assert "No persona configured." in prompt
    assert "## Global Response Rules\nGlobal rule" in prompt


def test_missing_global_response_rules_degrade_with_warning_and_size(tmp_path):
    result = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {})

    assert "## Global Response Rules\nGlobal response rules unavailable." in result.prompt
    assert "Missing context file: response_rules.md" in result.warnings
    assert result.section_sizes["response_rules"] == len("Global response rules unavailable.")


def test_action_tags_include_per_tag_guidance_and_keep_unknown_names(tmp_path):
    prompt = PromptBuilder(tmp_path).build(
        UserEvent(text="hello"), [], {}, action_tags=["laugh", "awkward", "speechless", "waving", "annoy", "listen", "custom"]
    ).prompt

    for tag in ("laugh", "awkward", "speechless", "waving", "annoy", "listen"):
        assert f"- {tag}: " in prompt
    assert "- custom\n" in prompt
    assert "觸發：多次重複、持續挑釁" in prompt
    assert "避免：普通好消息、禮貌附和" in prompt


def test_action_tags_are_none_when_unavailable(tmp_path):
    prompt = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {}).prompt

    assert "## Available Character Action Tags\nnone" in prompt


def test_output_contract_requires_traditional_chinese_taiwan_usage(tmp_path):
    prompt = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {}).prompt

    assert "繁體中文（台灣用語）" in prompt


def test_persona_present_drops_echoes_identity_claim(tmp_path):
    prompt = PromptBuilder(tmp_path).build(
        UserEvent(text="你是誰"), [], {}, persona="我是evan 我是華碩的虛擬歌姬",
    ).prompt

    assert "You are ECHOES, a local-first desktop companion." not in prompt
    assert "我是evan 我是華碩的虛擬歌姬" in prompt


def test_no_persona_keeps_default_echoes_identity(tmp_path):
    prompt = PromptBuilder(tmp_path).build(UserEvent(text="你是誰"), [], {}).prompt

    assert "You are ECHOES, a local-first desktop companion." in prompt


def test_memory_evidence_shows_attribute_without_retrieval_metadata(tmp_path):
    item = MemoryItem("m1", "miku", "default", "使用者.喜好.拉麵", "semantic", "我喜歡拉麵", "active", "e1", "2026-01-01T00:00:00+00:00")
    result = RetrievalResult([item], RetrievalTrace.empty("拉麵"))
    prompt = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {}, retrieval_result=result).prompt
    assert "[喜好] 我喜歡拉麵" in prompt
    assert "m1" not in prompt
    assert "score" not in prompt


def test_knowledge_evidence_is_isolated_from_retrieval_evidence(tmp_path):
    memory_item = MemoryItem("m1", "miku", "default", "使用者.喜好.拉麵", "semantic", "我喜歡拉麵", "active", "e1", "2026-01-01T00:00:00+00:00")
    knowledge_item = MemoryItem("k1", "shared", "default", "er_core_0001", "core_system", "生命力決定角色的最大 HP。", "active", None, "2026-01-01T00:00:00+00:00")
    result = RetrievalResult([memory_item], RetrievalTrace.empty("拉麵"))
    prompt = PromptBuilder(tmp_path).build(
        UserEvent(text="hello"), [], {}, retrieval_result=result, knowledge_evidence=[knowledge_item],
    ).prompt

    retrieval_section = prompt.split("## Retrieval Evidence")[1].split("## Knowledge Reference")[0]
    knowledge_section = prompt.split("## Knowledge Reference")[1].split("## User Text")[0]
    assert "我喜歡拉麵" in retrieval_section and "生命力決定角色的最大 HP" not in retrieval_section
    assert "生命力決定角色的最大 HP" in knowledge_section and "我喜歡拉麵" not in knowledge_section
    assert "[core_system] 生命力決定角色的最大 HP。" in prompt
    assert prompt.index("## Retrieval Evidence") < prompt.index("## Knowledge Reference") < prompt.index("## User Text")


def test_history_instruction_forbids_asking_back_when_answer_already_given(tmp_path):
    prompt = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {}).prompt

    instruction = "Do not ask them what they like/mean/want when the answer is already given above."
    assert instruction in prompt
    assert prompt.index("## Conversation History") < prompt.index(instruction) < prompt.index("## Retrieval Evidence")


def test_knowledge_instruction_forbids_stonewalling_when_content_is_available(tmp_path):
    """實測 bug:gemma3:12b 拿到知識內容仍回「你想從哪開始?」,完全不引用。

    Global Response Rules 的「不空轉」規則離 Knowledge Reference 隔了好幾個
    區塊,對小模型形同不存在;指示必須貼著知識內容本身重申一次。
    """
    knowledge_item = MemoryItem("k1", "shared", "default", "er_faith_0001", "core_system", "信仰是施放禱告的核心屬性。", "active", None, "2026-01-01T00:00:00+00:00")
    prompt = PromptBuilder(tmp_path).build(
        UserEvent(text="還有其他內容嗎?"), [], {}, knowledge_evidence=[knowledge_item],
    ).prompt

    knowledge_section = prompt.split("## Knowledge Reference")[1].split("## User Text")[0]
    assert "never respond with only a clarifying question" in knowledge_section
    assert "信仰是施放禱告的核心屬性" in knowledge_section


def test_knowledge_reference_is_none_when_no_evidence(tmp_path):
    prompt = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {}).prompt
    knowledge_section = prompt.split("## Knowledge Reference")[1].split("## User Text")[0]
    assert knowledge_section.strip().endswith("none")


def test_media_clarification_reaches_the_prompt_so_the_reply_asks_instead_of_guessing(tmp_path):
    """路由判定媒體意圖不明確時不執行工具,但回覆必須把缺的那一項問出來。"""
    builder = PromptBuilder(tmp_path)

    missing_query = builder.build(UserEvent(text="播放音樂"), [], {}, media_clarification="missing_music_query").prompt
    conflict = builder.build(UserEvent(text="播新聞和音樂"), [], {}, media_clarification="conflict").prompt
    ordinary = builder.build(UserEvent(text="你好"), [], {}, media_clarification="none").prompt

    assert "沒有指定歌曲或類型" in missing_query
    assert "要先做哪一個" in conflict
    # 一般回合不得混進澄清指引
    assert "沒有指定歌曲或類型" not in ordinary and "要先做哪一個" not in ordinary
