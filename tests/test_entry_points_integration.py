"""Entry points hand off to the window's application command methods."""

from types import SimpleNamespace

from ui.transparent_window import TransparentWindow


def test_quick_intent_bridge_and_overlay_aliases_reach_their_command_entries():
    quick_calls, skill_calls = [], []
    window = SimpleNamespace(
        trigger_cached_intent=lambda name, source: quick_calls.append((name, source)),
        trigger_enabled_skill_for_behavior=lambda behavior: skill_calls.append(behavior),
    )

    TransparentWindow.trigger_quick_intent_from_bridge(window, "joke")
    TransparentWindow.trigger_overlay_action_from_bridge(window, "music")
    TransparentWindow.trigger_overlay_action_from_bridge(window, "news")

    assert quick_calls == [("joke", "joke 面板觸發")]
    assert skill_calls == ["play_music", "report_news"]


def test_submit_agentic_text_sends_a_conversation_command_to_action_bus():
    commands = []
    window = SimpleNamespace(
        _interaction_worker=None,
        _action_bus=SimpleNamespace(execute=lambda command: commands.append(command) or SimpleNamespace(status="ok")),
        _conversation_pending=False,
        _set_agentic_busy=lambda active: None,
        set_action_status=lambda *args, **kwargs: None,
    )

    TransparentWindow.submit_agentic_text(window, "hello")

    assert [(command.action, command.text, command.source) for command in commands] == [
        ("conversation", "hello", "ui")
    ]
