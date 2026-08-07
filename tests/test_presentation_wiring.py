from unittest.mock import MagicMock

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


class _Window:
    def __init__(self): self.calls = []
    def _on_action_bus_conversation(self, payload): self.calls.append(("conversation", payload))
    def _on_action_bus_error(self, message, character_id=None): self.calls.append(("error", message))


def test_binder_only_observes_presentation_events():
    bus, window = SimpleEventBus(), _Window()
    PresentationEventBinder(window, bus)
    bus.publish(AppEvent("EVT_CONVERSATION_TURN", "t", {"reply": "hi"}))
    bus.publish(AppEvent("EVT_RUNTIME_ERROR", "t", {"message": "boom"}))

    assert window.calls == [("conversation", {"reply": "hi"}), ("error", "boom")]
