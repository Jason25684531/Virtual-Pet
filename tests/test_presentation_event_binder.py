from pet_harness.app.event_bus import SimpleEventBus
from pet_harness.app.events import AppEvent
from ui.presentation_event_binder import PresentationEventBinder


class _Window:
    def __init__(self): self.calls = []
    def _dispatch_action_legacy(self, *args, **kwargs): self.calls.append(("dispatch", args, kwargs))
    def _trigger_cached_intent_legacy(self, *args): self.calls.append(("cached", args, {}))
    def _reset_runtime_state_legacy(self): self.calls.append(("reset", (), {}))


def test_binder_routes_application_events_to_legacy_presentation_shim():
    bus, window = SimpleEventBus(), _Window()
    PresentationEventBinder(window, bus)
    bus.publish(AppEvent("EVT_ACTION_REQUESTED", "t", {"action": "laugh", "text": "hi"}))
    bus.publish(AppEvent("EVT_ACTION_REQUESTED", None, {"action": "cached_joke", "source": "button"}))
    bus.publish(AppEvent("EVT_RESET_REQUESTED"))

    assert window.calls[0][1][0] == "[ACTION:laugh] hi"
    assert window.calls[1] == ("cached", ("joke", "button"), {})
    assert window.calls[2][0] == "reset"
