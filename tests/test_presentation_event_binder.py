from pet_harness.app.event_bus import SimpleEventBus
from pet_harness.app.events import AppEvent
from ui.presentation_event_binder import PresentationEventBinder


class _Window:
    def __init__(self): self.calls = []
    def _on_action_bus_conversation(self, payload): self.calls.append(("conversation", payload))
    def _on_action_bus_error(self, message): self.calls.append(("error", message))


def test_binder_only_observes_presentation_events():
    bus, window = SimpleEventBus(), _Window()
    PresentationEventBinder(window, bus)
    bus.publish(AppEvent("EVT_CONVERSATION_TURN", "t", {"reply": "hi"}))
    bus.publish(AppEvent("EVT_RUNTIME_ERROR", "t", {"message": "boom"}))

    assert window.calls == [("conversation", {"reply": "hi"}), ("error", "boom")]
