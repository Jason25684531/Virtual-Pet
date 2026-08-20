"""Presentation adapters that wire the app's events and motion port to the window."""

from pet_harness.app.event_bus import SimpleEventBus
from pet_harness.app.events import AppEvent


class MotionPortAdapter:
    def __init__(self, coordinator, window) -> None:
        self._coordinator, self._window = coordinator, window

    def dispatch_directive(self, directive, *, trace_id=None, allow_tts=True, wait_for_tts_start=False):
        return self._coordinator.dispatch(directive, trace_id=trace_id, allow_tts=allow_tts, wait_for_tts_start=wait_for_tts_start)

    def trigger_cached_intent(self, intent_name, source):
        return self._coordinator.trigger_cached_intent(intent_name, source)

    def speak(self, text, *, trace_id=None, has_action=False):
        self._coordinator.speak_text(text, trace_id=trace_id, has_action=has_action)

    def reset(self):
        self._coordinator.reset_runtime_state()
        self._window.reset_presentation()


class PresentationEventBinder:
    def __init__(self, window, events: SimpleEventBus) -> None:
        self._window = window
        events.subscribe("EVT_CONVERSATION_TURN", self._on_conversation)
        events.subscribe("EVT_RUNTIME_ERROR", self._on_error)

    def _on_conversation(self, event: AppEvent) -> None:
        payload = dict(event.payload)
        payload["trace_id"] = event.trace_id
        self._window._on_action_bus_conversation(payload)

    def _on_error(self, event: AppEvent) -> None:
        self._window._on_action_bus_error(str(event.payload.get("message") or "Interaction failed."), event.payload.get("character_id"))
