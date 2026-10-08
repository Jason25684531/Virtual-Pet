"""Chat 快捷 tag 的固定文案:顯示一輪、直接 TTS、不呼叫 LLM。"""

from unittest.mock import MagicMock

from ui.transparent_window import TransparentWindow


def _window():
    fake = MagicMock()
    fake.get_current_character_id.return_value = "miku"
    fake.dispatch_action.return_value = True
    return fake


def test_say_fixed_text_interrupts_shows_turn_and_speaks_without_llm():
    fake = _window()

    TransparentWindow.say_fixed_text(fake, "遊戲攻略介紹", "《艾爾登法環》是一款遊戲。")

    fake._action_bus.cancel_conversation.assert_called_once()
    fake.show_synthetic_conversation_turn.assert_called_once_with("Quick", "遊戲攻略介紹", "《艾爾登法環》是一款遊戲。")
    directive = fake.dispatch_action.call_args.args[0]
    assert directive == "[ACTION:wave_response] 《艾爾登法環》是一款遊戲。"
    assert fake.dispatch_action.call_args.kwargs["allow_tts"] is True
    fake._log_assistant_utterance.assert_called_once_with("《艾爾登法環》是一款遊戲。")
    fake._adapter.prepare_turn.assert_not_called()
    fake._action_bus.execute.assert_not_called()


def test_say_fixed_text_falls_back_to_speak_text_when_dispatch_fails():
    fake = _window()
    fake.dispatch_action.return_value = False

    TransparentWindow.say_fixed_text(fake, "tag", "文案")

    assert fake.speak_text.call_args.args == ("文案",)


def test_say_fixed_text_ignores_empty_input_or_missing_character():
    fake = _window()
    TransparentWindow.say_fixed_text(fake, "tag", "")
    fake.get_current_character_id.return_value = None
    TransparentWindow.say_fixed_text(fake, "tag", "文案")

    fake.show_synthetic_conversation_turn.assert_not_called()
    fake.dispatch_action.assert_not_called()


def test_say_fixed_text_uses_requested_trace_prefix():
    fake = _window()

    TransparentWindow.say_fixed_text(fake, "demo", "文案", trace_prefix="demo")

    assert fake.dispatch_action.call_args.kwargs["trace_id"].startswith("demo-")
