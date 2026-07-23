"""P0 baseline: shutdown remains STT -> dispatcher -> adapter until lifecycle owns it."""

from ui.transparent_window import TransparentWindow


class _Recorder:
    def __init__(self, events, name):
        self._events = events
        self._name = name

    def shutdown(self):
        self._events.append(self._name)


def test_window_shutdown_keeps_legacy_order():
    events = []

    class Window:
        _stt_controller = _Recorder(events, "stt")
        _action_dispatcher = _Recorder(events, "dispatcher")
        _adapter = _Recorder(events, "adapter")

    TransparentWindow.shutdown_background_tasks(Window())

    assert events == ["stt", "dispatcher", "adapter"]
