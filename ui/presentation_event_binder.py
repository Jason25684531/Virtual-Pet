"""Presentation-side subscriber for application events."""

from pet_harness.app.event_bus import EventBus
from pet_harness.app.events import AppEvent


class PresentationEventBinder:
    def __init__(self, window, events: EventBus) -> None:
        self._window = window
        events.subscribe("EVT_ACTION_REQUESTED", self._on_action)
        events.subscribe("EVT_RESET_REQUESTED", self._on_reset)
        events.subscribe("EVT_CONVERSATION_TURN", self._on_conversation)
        events.subscribe("EVT_RUNTIME_ERROR", self._on_error)

    def _on_action(self, event: AppEvent) -> None:
        payload = event.payload
        action = str(payload["action"])
        if action.startswith("cached_"):
            self._window._trigger_cached_intent_legacy(action.removeprefix("cached_"), str(payload.get("source") or "action_bus"))
            return
        directive = f"[ACTION:{action}] {payload.get('text') or ''}".strip()
        self._window._dispatch_action_legacy(
            directive,
            trace_id=event.trace_id,
            allow_tts=bool(payload.get("allow_tts", True)),
            wait_for_tts_start=bool(payload.get("wait_for_tts_start", False)),
        )

    def _on_reset(self, _event: AppEvent) -> None:
        self._window._reset_runtime_state_legacy()

    def _on_conversation(self, event: AppEvent) -> None:
        self._window._on_action_bus_conversation(dict(event.payload))

    def _on_error(self, event: AppEvent) -> None:
        self._window._on_action_bus_error(str(event.payload.get("message") or "Interaction failed."))
