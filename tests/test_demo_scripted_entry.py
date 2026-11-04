from types import SimpleNamespace
from unittest.mock import Mock

from ui.transparent_window import TransparentWindow


def test_demo_match_waits_before_using_fixed_reply(monkeypatch):
    say_fixed_text = Mock()
    execute = Mock()
    scheduled = []
    window = SimpleNamespace(
        say_fixed_text=say_fixed_text,
        _action_bus=SimpleNamespace(execute=execute, cancel_conversation=Mock()),
        _motion_coordinator=SimpleNamespace(interrupt_all=Mock(), finish_streaming_trace=Mock()),
        _conversation_pending=False,
        _conversation_character_id=None,
        _conversation_trace_id=None,
        _proactive_greeting_active=False,
        _greeter=SimpleNamespace(reset=Mock()),
        _proactive_greeting_release_timer=SimpleNamespace(stop=Mock(), start=Mock()),
        _set_agentic_busy=Mock(),
        begin_conversation_turn=Mock(),
        set_conversation_assistant=Mock(),
        finish_conversation_turn=Mock(),
        _finish_conversation_for=Mock(),
        set_action_status=Mock(),
        get_current_character_id=lambda: "Choppr",
        stop_motion_loop=Mock(),
        restore_idle_video=Mock(),
    )
    monkeypatch.setattr(
        "ui.transparent_window.QTimer.singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    TransparentWindow.submit_agentic_text(window, "Any cute home decor you'd recommend?")

    say_fixed_text.assert_not_called()
    assert scheduled[0][0] == 2200
    scheduled[0][1]()
    say_fixed_text.assert_called_once()
    assert say_fixed_text.call_args.args[0] == "Any cute home decor you'd recommend?"
    assert say_fixed_text.call_args.kwargs == {"trace_prefix": "demo", "show_turn": False}
    window.begin_conversation_turn.assert_called_once()
    execute.assert_not_called()
