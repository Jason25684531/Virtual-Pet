"""主動打招呼與使用者輸入的優先關係（specs/proactive-conversation）。

沿用既有慣例（見 test_transparent_window_stt_states.py）：以 unbound method
搭配假 self（MagicMock）驗證，不需建構真正的 QWebEngineView。
"""

from unittest.mock import MagicMock

from pet_harness.app.commands import ActionResult
from ui.transparent_window import TransparentWindow


def _make_fake_window(greeting_active=False, stt_available=True, stt_state="idle"):
    fake = MagicMock()
    fake._proactive_greeting_active = greeting_active
    fake._conversation_pending = False
    fake._conversation_trace_id = None
    fake._stt_available = stt_available
    fake._stt_state = stt_state
    fake._motion_coordinator = MagicMock(has_active_motion=False, is_tts_busy=False)
    fake.get_current_character_id.return_value = "Choppr"
    fake._action_bus.execute.return_value = ActionResult("ok")
    return fake


def _conversation_commands(fake):
    return [
        call.args[0]
        for call in fake._action_bus.execute.call_args_list
        if getattr(call.args[0], "action", None) == "conversation"
    ]


def test_submit_during_greeting_is_accepted_not_discarded():
    """#12 的核心：打招呼進行中送出的文字必須被受理，不能被靜默丟棄。"""
    fake = _make_fake_window(greeting_active=True)

    TransparentWindow.submit_agentic_text(fake, "英雄聯盟這次改版怎麼樣")

    commands = _conversation_commands(fake)
    assert len(commands) == 1
    assert commands[0].text == "英雄聯盟這次改版怎麼樣"


def test_submit_during_greeting_interrupts_the_greeting():
    fake = _make_fake_window(greeting_active=True)

    TransparentWindow.submit_agentic_text(fake, "在嗎")

    fake._action_bus.cancel_conversation.assert_called_once()
    fake._motion_coordinator.interrupt_all.assert_called_once()


def test_submit_during_greeting_shows_no_wait_prompt():
    fake = _make_fake_window(greeting_active=True)

    TransparentWindow.submit_agentic_text(fake, "在嗎")

    messages = [call.args[0] for call in fake.set_action_status.call_args_list if call.args]
    assert "請等我說完再輸入。" not in messages


def test_interrupt_clears_the_greeting_busy_flag():
    """旗標留在 True 會讓 is_busy 持續為真，剛送出的回合會被誤判成還在忙。"""
    fake = _make_fake_window(greeting_active=True)

    TransparentWindow._interrupt_active_conversation(fake)

    assert fake._proactive_greeting_active is False
    fake._proactive_greeting_release_timer.stop.assert_called_once()


def test_submit_restarts_the_proactive_timer():
    """自動發話改以使用者最後一次送出為基準（t+30s）。"""
    fake = _make_fake_window()

    TransparentWindow.submit_agentic_text(fake, "哈囉")

    fake._greeter.reset.assert_called_once()


def test_rejected_submit_does_not_restart_the_timer():
    fake = _make_fake_window()
    fake.get_current_character_id.return_value = None

    TransparentWindow.submit_agentic_text(fake, "哈囉")

    fake._greeter.reset.assert_not_called()


def test_empty_submit_does_not_restart_the_timer():
    fake = _make_fake_window()

    TransparentWindow.submit_agentic_text(fake, "   ")

    fake._greeter.reset.assert_not_called()


def test_starting_voice_input_restarts_the_proactive_timer():
    fake = _make_fake_window()

    TransparentWindow._handle_stt_button_clicked(fake)

    fake.stt_start_requested.emit.assert_called_once()
    fake._greeter.reset.assert_called_once()


def test_voice_input_during_greeting_is_accepted():
    fake = _make_fake_window(greeting_active=True)

    TransparentWindow._handle_stt_button_clicked(fake)

    fake.stt_start_requested.emit.assert_called_once()
    messages = [call.args[0] for call in fake.set_action_status.call_args_list if call.args]
    assert "請等我說完再輸入。" not in messages


def test_greeter_reset_restarts_a_running_timer():
    """reset() 是 t+30s 的來源：計時中被重設要 stop 再 start。"""
    from ui.proactive_greeter import ProactiveGreeter

    greeter = ProactiveGreeter(MagicMock(), lambda: False, ["嗨"], 30.0)
    greeter._timer = MagicMock()
    greeter._timer.isActive.return_value = True

    greeter.reset()

    greeter._timer.stop.assert_called_once()
    greeter._timer.start.assert_called_once()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
