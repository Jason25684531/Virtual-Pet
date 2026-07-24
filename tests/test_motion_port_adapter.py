from unittest.mock import MagicMock

from ui.motion_port_adapter import MotionPortAdapter


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
    window.clear_conversation_turns.assert_called_once()
