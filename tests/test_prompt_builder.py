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
