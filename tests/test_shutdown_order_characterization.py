"""P0 baseline: shutdown remains STT -> dispatcher -> adapter until lifecycle owns it."""

from pet_harness.app.runtime_lifecycle import CallbackRuntime, RuntimeLifecycle


class _Recorder:
    def __init__(self, events, name):
        self._events = events
        self._name = name

    def shutdown(self):
        self._events.append(self._name)


def test_lifecycle_shutdown_is_idempotent_and_keeps_reverse_order():
    events = []
    lifecycle = RuntimeLifecycle()
    lifecycle.register(CallbackRuntime("adapter", lambda _wait: events.append("adapter")))
    lifecycle.register(CallbackRuntime("motion", lambda _wait: events.append("motion")))
    lifecycle.register(CallbackRuntime("stt", lambda _wait: events.append("stt")))

    lifecycle.shutdown_all()
    lifecycle.shutdown_all()

    assert events == ["stt", "motion", "adapter"]
