from types import SimpleNamespace

from pet_harness.app.application_coordinator import ApplicationCoordinator
from pet_harness.app.commands import ActionCommand
from pet_harness.app.runtime_lifecycle import CallbackRuntime
from pet_harness.memory.base_memory_store import NullMemoryStore
from pet_harness.runtime.provider_runtime import ProviderRuntime
from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
from tests.conftest import FakeProvider
from tests.test_harness_per_character import harness_env  # noqa: F401
from ui.transparent_window import TransparentWindow


class _TrackingStore(NullMemoryStore):
    def __init__(self):
        self.turns = []

    def save_turn(self, event_id, user_text, reply):
        self.turns.append((event_id, user_text, reply))


class _QueuedExecutor:
    name = "executor"

    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.accepting = True

    def start(self):
        return None

    def stop(self, wait_ms=5000):
        self.accepting = False
        self.events.append("executor")

    def submit(self, job, on_done):
        if not self.accepting:
            raise RuntimeError("executor stopped")
        self.job, self.on_done = job, on_done

    def complete(self):
        try:
            self.on_done(True, "", self.job())
        except Exception as exc:  # mirrors the production executor contract
            self.on_done(False, str(exc), None)


def test_conversation_stays_bound_to_submitted_character_and_releases_retired_engine(harness_env, monkeypatch):
    _tmp_path, agentic_root = harness_env
    stores = {}

    def memory_factory(character_id, _profile):
        return stores.setdefault(character_id, _TrackingStore())

    coordinator = ApplicationCoordinator(
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
        agentic_root=agentic_root,
        default_character_id="Choppr",
        memory_store_factory=memory_factory,
    )
    adapter = PyQtHarnessAdapter(
        agentic_root=agentic_root,
        provider_runtime=coordinator.provider_runtime,
        character_router=coordinator.character_router,
        character_registry=coordinator.character_registry,
    )
    executor, events = _QueuedExecutor(), []
    coordinator.configure_conversation(adapter, executor)
    coordinator.event_bus.subscribe("EVT_CONVERSATION_TURN", events.append)

    engine_a = coordinator.character_router.get_active_engine()
    shutdown_calls = []
    original_shutdown = engine_a.shutdown
    monkeypatch.setattr(engine_a, "shutdown", lambda: (shutdown_calls.append(True), original_shutdown())[1])

    assert coordinator.action_bus.execute(ActionCommand(
        "conversation", "tell me a joke", character_id="Choppr"
    )).status == "ok"
    coordinator.character_router.switch_character("miku")
    engine_b = coordinator.character_router.get_active_engine()

    assert shutdown_calls == []
    assert engine_b._character_id == "miku"
    monkeypatch.setattr(
        coordinator.character_router,
        "get_active_engine",
        lambda: (_ for _ in ()).throw(AssertionError("background turn read active engine")),
    )
    executor.complete()

    payload = events[0].payload
    assert payload["character_id"] == "Choppr"
    assert payload["saved_to_db"] is True
    assert payload["xp_delta"] > 0
    assert stores["Choppr"].turns
    assert shutdown_calls == [True]


def test_stale_conversation_result_does_not_update_new_character_ui():
    calls = []
    window = SimpleNamespace(
        _conversation_pending=True,
        _conversation_character_id="Choppr",
        get_current_character_id=lambda: "miku",
        consume_interaction_result=lambda payload, message: calls.append((payload, message)),
        _set_agentic_busy=lambda busy: calls.append(("busy", busy)),
    )
    window._is_current_conversation_character = lambda character_id: character_id == "miku"
    window._finish_conversation_for = lambda _character_id: setattr(window, "_conversation_pending", False)

    TransparentWindow._on_action_bus_conversation(window, {"character_id": "Choppr", "reply": "A reply"})

    assert calls == []
    assert window._conversation_pending is False


def test_application_shutdown_defers_leased_engine_until_turn_finishes(harness_env, monkeypatch):
    _tmp_path, agentic_root = harness_env
    coordinator = ApplicationCoordinator(
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
        agentic_root=agentic_root,
        default_character_id="Choppr",
    )
    adapter = PyQtHarnessAdapter(
        agentic_root=agentic_root,
        provider_runtime=coordinator.provider_runtime,
        character_router=coordinator.character_router,
        character_registry=coordinator.character_registry,
    )
    shutdown_order = []
    executor = _QueuedExecutor(shutdown_order)
    coordinator.configure_conversation(adapter, executor)
    coordinator.lifecycle.register(CallbackRuntime(
        "router", lambda _wait: (shutdown_order.append("router"), coordinator.character_router.shutdown())[1]
    ))
    coordinator.lifecycle.register(executor)
    engine = coordinator.character_router.get_active_engine()
    closed = []
    original_shutdown = engine.shutdown
    monkeypatch.setattr(engine, "shutdown", lambda: (closed.append(True), original_shutdown())[1])

    coordinator.action_bus.execute(ActionCommand("conversation", "tell me a joke", character_id="Choppr"))
    coordinator.shutdown()

    assert shutdown_order == ["executor", "router"]
    assert closed == []
    executor.complete()
    assert closed == [True]
