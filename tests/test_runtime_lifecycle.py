from pet_harness.app.runtime_lifecycle import CallbackRuntime, RuntimeLifecycle
from pet_harness.character.router import CharacterRouter
from unittest.mock import MagicMock


class _Runtime:
    def __init__(self, name, events, fail_stop=False):
        self._name, self._events, self._fail_stop = name, events, fail_stop

    @property
    def name(self):
        return self._name

    def start(self):
        self._events.append(f"start:{self.name}")

    def stop(self, wait_ms=5000):
        self._events.append(f"stop:{self.name}:{wait_ms}")
        if self._fail_stop:
            raise TimeoutError(self.name)


def test_lifecycle_stops_in_reverse_registration_order():
    events = []
    lifecycle = RuntimeLifecycle()
    lifecycle.register(_Runtime("stt", events))
    lifecycle.register(_Runtime("audio", events))
    lifecycle.register(_Runtime("provider", events))

    lifecycle.shutdown_all(100)

    assert events == [
        "stop:provider:100", "stop:audio:100", "stop:stt:100",
    ]


def test_lifecycle_continues_after_a_runtime_stop_failure():
    events = []
    lifecycle = RuntimeLifecycle()
    lifecycle.register(_Runtime("first", events))
    lifecycle.register(_Runtime("broken", events, fail_stop=True))
    lifecycle.register(_Runtime("last", events))

    lifecycle.shutdown_all()

    assert events == ["stop:last:5000", "stop:broken:5000", "stop:first:5000"]


def test_callback_runtime_adapts_legacy_stop_signature():
    waits = []
    runtime = CallbackRuntime("legacy", waits.append)

    runtime.stop(123)

    assert runtime.name == "legacy"
    assert waits == [123]


def test_lifecycle_closes_active_router_engine_once():
    router = CharacterRouter()
    engine = MagicMock()
    router._active_engine = engine
    lifecycle = RuntimeLifecycle()
    lifecycle.register(CallbackRuntime("router", lambda _wait: router.shutdown()))

    lifecycle.shutdown_all()
    lifecycle.shutdown_all()

    engine.shutdown.assert_called_once()
    assert router.get_active_engine() is None
