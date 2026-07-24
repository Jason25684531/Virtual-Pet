"""Presentation-side subscriber for application events."""

from pet_harness.app.event_bus import EventBus
from pet_harness.app.events import AppEvent


class PresentationEventBinder:
    def __init__(self, window, events: EventBus) -> None:
        self._window = window
        events.subscribe("EVT_CONVERSATION_TURN", self._on_conversation)
        events.subscribe("EVT_RUNTIME_ERROR", self._on_error)

    def _on_conversation(self, event: AppEvent) -> None:
        self._window._on_action_bus_conversation(dict(event.payload))

    def _on_error(self, event: AppEvent) -> None:
        self._window._on_action_bus_error(str(event.payload.get("message") or "Interaction failed."))
