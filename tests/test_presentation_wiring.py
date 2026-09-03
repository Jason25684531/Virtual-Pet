from unittest.mock import MagicMock
from types import SimpleNamespace

from pet_harness.app.event_bus import SimpleEventBus
from pet_harness.app.events import AppEvent
from ui.presentation_wiring import MotionPortAdapter, PresentationEventBinder


def test_motion_port_adapter_delegates_to_existing_coordinator():
    coordinator, window = MagicMock(), MagicMock()
    coordinator.dispatch.return_value = True
    adapter = MotionPortAdapter(coordinator, window)

    assert adapter.dispatch_directive("[ACTION:laugh]", trace_id="t") is True
    adapter.trigger_cached_intent("joke", "test")
    adapter.speak("hello", has_action=True)
    adapter.reset()

    coordinator.dispatch.assert_called_once_with("[ACTION:laugh]", trace_id="t", allow_tts=True, wait_for_tts_start=False)
    coordinator.trigger_cached_intent.assert_called_once_with("joke", "test")
    coordinator.speak_text.assert_called_once_with("hello", trace_id=None, has_action=True)
    coordinator.reset_runtime_state.assert_called_once()
    window.reset_presentation.assert_called_once()


def test_reset_presentation_clears_stt_conversation_busy_and_ui_route():
    calls = []
    window = SimpleNamespace(
        _stt_state="listening",
        _conversation_pending=True,
        _conversation_character_id="Choppr",
        _conversation_trace_id="trace-1",
        stt_stop_requested=SimpleNamespace(emit=lambda: calls.append("stt-stop")),
        _set_agentic_busy=lambda busy: calls.append(("busy", busy)),
        set_conversation_queue_depth=lambda depth: calls.append(("queue", depth)),
        _hide_developer_input=lambda: calls.append("hide-input"),
        stop_music=lambda: calls.append("stop-music"),
        stop_motion_loop=lambda: calls.append("stop-motion"),
        clear_panel_video=lambda: calls.append("clear-panel"),
        clear_conversation_turns=lambda: calls.append("clear-turns"),
        _run_javascript=lambda name: calls.append(name),
        restore_idle_video=lambda: calls.append("restore-idle"),
        set_action_status=lambda *_args, **_kwargs: calls.append("status"),
    )

    from ui.transparent_window import TransparentWindow
    TransparentWindow.reset_presentation(window)

    assert calls[:2] == ["stt-stop", ("busy", False)]
    assert "resetUiRoute" in calls
    assert window._conversation_pending is False
    assert window._conversation_character_id is None
    assert window._conversation_trace_id is None


def test_clear_chat_history_only_clears_rendered_turns():
    calls = []
    window = SimpleNamespace(
        clear_conversation_turns=lambda: calls.append("clear-turns"),
        set_action_status=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    from ui.transparent_window import TransparentWindow
    TransparentWindow.clear_chat_history(window)

    assert calls[0] == "clear-turns"
    assert calls[1][0][0] == "聊天室已清空。"


class _Window:
    def __init__(self): self.calls = []
    def _on_action_bus_conversation(self, payload): self.calls.append(("conversation", payload))
    def _on_action_bus_error(self, message, character_id=None): self.calls.append(("error", message))


def test_binder_only_observes_presentation_events():
    bus, window = SimpleEventBus(), _Window()
    PresentationEventBinder(window, bus)
    bus.publish(AppEvent("EVT_CONVERSATION_TURN", "t", {"reply": "hi"}))
    bus.publish(AppEvent("EVT_RUNTIME_ERROR", "t", {"message": "boom"}))

    assert window.calls == [("conversation", {"reply": "hi", "trace_id": "t"}), ("error", "boom")]
