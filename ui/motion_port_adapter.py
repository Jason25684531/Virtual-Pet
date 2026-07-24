"""Presentation adapter for the existing MotionCoordinator state machine."""

from pet_harness.app.ports.motion_port import MotionPort


class MotionPortAdapter(MotionPort):
    def __init__(self, coordinator, window) -> None:
        self._coordinator, self._window = coordinator, window

    def dispatch_directive(self, directive, *, trace_id=None, allow_tts=True, wait_for_tts_start=False):
        return self._coordinator.dispatch(
            directive, trace_id=trace_id, allow_tts=allow_tts,
            wait_for_tts_start=wait_for_tts_start,
        )

    def trigger_cached_intent(self, intent_name, source):
        return self._coordinator.trigger_cached_intent(intent_name, source)

    def speak(self, text, *, trace_id=None, has_action=False):
        self._coordinator.speak_text(text, trace_id=trace_id, has_action=has_action)

    def reset(self):
        self._coordinator.reset_runtime_state()
        self._window.reset_presentation()
