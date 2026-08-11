from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.models.events import UserEvent


def test_output_contract_requires_traditional_chinese_taiwan_usage(tmp_path):
    prompt = PromptBuilder(tmp_path).build(UserEvent(text="hello"), [], {}).prompt

    assert "繁體中文（台灣用語）" in prompt
