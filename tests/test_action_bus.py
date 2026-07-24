from pet_harness.app.action_bus import ActionBus
from pet_harness.app.application_coordinator import ApplicationCoordinator
from pet_harness.app.action_handler import ActionHandler
from pet_harness.app.commands import ActionCommand
from pet_harness.app.events import AppEvent
from pet_harness.app.event_bus import SimpleEventBus
from pet_harness.app.results import ActionResult
from tests.fakes.fake_background_executor import FakeBackgroundExecutor
from tests.conftest import FakeProvider
from tests.test_harness_per_character import harness_env  # noqa: F401
from pet_harness.runtime.provider_runtime import ProviderRuntime


def test_event_bus_delivers_events_in_subscription_order():
    bus = SimpleEventBus()
    received = []
    bus.subscribe("ready", received.append)
    event = AppEvent("ready", "trace-1", {"ok": True})

    bus.publish(event)

    assert received == [event]


def test_background_executor_fake_reports_result_and_failure():
    executor = FakeBackgroundExecutor()
    results = []
    executor.submit(lambda: "ok", lambda *result: results.append(result))
    executor.submit(lambda: 1 / 0, lambda *result: results.append(result))

    assert results[0] == (True, "", "ok")
    assert results[1][0] is False
    assert results[1][2] is None


def test_action_bus_rejects_unknown_actions_and_isolates_handler_errors():
    bus = SimpleEventBus()
    errors = []
    bus.subscribe("EVT_RUNTIME_ERROR", errors.append)
    actions = ActionBus(bus)
    assert actions.execute(ActionCommand("missing")) == ActionResult("rejected", "unknown_action")

    class BrokenHandler(ActionHandler):
        def can_handle(self, command): return command.action == "broken"
        def handle(self, command): raise RuntimeError("boom")

    actions.register(BrokenHandler())
    assert actions.execute(ActionCommand("broken")).status == "failed"
    assert errors[0].payload == {"message": "boom"}


def test_coordinator_builds_headless_domain_composition(harness_env):
    _tmp_path, agentic_root = harness_env
    runtime = ProviderRuntime(provider=FakeProvider())

    coordinator = ApplicationCoordinator(
        provider_runtime=runtime,
        agentic_root=agentic_root,
        default_character_id="Choppr",
    )

    assert coordinator.provider_runtime is runtime
    assert coordinator.character_router.get_active_snapshot().character_id == "Choppr"
    assert coordinator.action_bus.execute(ActionCommand("unknown")).reason == "unknown_action"


def test_coordinator_action_handlers_publish_requests_without_ui_calls(harness_env):
    _tmp_path, agentic_root = harness_env
    coordinator = ApplicationCoordinator(provider_runtime=ProviderRuntime(provider=FakeProvider()), agentic_root=agentic_root)
    calls = []
    class Motion:
        def dispatch_directive(self, directive, **kwargs): calls.append((directive, kwargs)); return True
        def trigger_cached_intent(self, *_args): return True
        def speak(self, *_args, **_kwargs): pass
        def reset(self): pass
    coordinator.configure_motion(Motion())

    result = coordinator.action_bus.execute(ActionCommand("report_news", trace_id="news-1", source="shortcut"))

    assert result.status == "ok"
    assert calls == [("[ACTION:report_news]", {"trace_id": "news-1", "allow_tts": True, "wait_for_tts_start": False})]


def test_conversation_handler_publishes_completed_turn(harness_env):
    _tmp_path, agentic_root = harness_env
    coordinator = ApplicationCoordinator(
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
        agentic_root=agentic_root,
        default_character_id="Choppr",
    )
    turns = []
    coordinator.event_bus.subscribe("EVT_CONVERSATION_TURN", turns.append)
    class Conversation:
        def prepare_turn(self, text, source, character_id):
            return lambda: {"reply": text, "xp_display": {}, "character_id": character_id}

    coordinator.configure_conversation(Conversation(), FakeBackgroundExecutor())

    result = coordinator.action_bus.execute(ActionCommand(
        "conversation", "tell me a joke", trace_id="turn-1", character_id="Choppr"
    ))

    assert result.status == "ok"
    assert turns[0].trace_id == "turn-1"
    assert turns[0].payload["character_id"] == "Choppr"
