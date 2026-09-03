from pet_harness.app.action_bus import ActionBus
from pet_harness.app.application_coordinator import ApplicationCoordinator
from pet_harness.app.action_handler import ActionHandler
from pet_harness.app.commands import ActionCommand
from pet_harness.app.events import AppEvent
from pet_harness.app.event_bus import SimpleEventBus
from pet_harness.app.handlers import ResetHandler
from pet_harness.app.ports import PreparedTurn
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
            return PreparedTurn(lambda: {"reply": text, "xp_display": {}, "character_id": character_id}, lambda: None)

    coordinator.configure_conversation(Conversation(), FakeBackgroundExecutor())

    result = coordinator.action_bus.execute(ActionCommand(
        "conversation", "tell me a joke", trace_id="turn-1", character_id="Choppr"
    ))

    assert result.status == "ok"
    assert turns[0].trace_id == "turn-1"
    assert turns[0].payload["character_id"] == "Choppr"


def test_reset_cancels_inflight_conversation_before_resetting_motion(harness_env):
    _tmp_path, agentic_root = harness_env
    coordinator = ApplicationCoordinator(
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
        agentic_root=agentic_root,
        default_character_id="Choppr",
    )
    order, turns = [], []

    class DeferredExecutor:
        def __init__(self): self.jobs = []
        def submit(self, job, on_done): self.jobs.append((job, on_done))

    class Conversation:
        def prepare_turn(self, text, source, character_id):
            return PreparedTurn(
                lambda: {"reply": text},
                lambda: None,
                lambda: order.append("cancel"),
            )

    class Motion:
        def reset(self): order.append("reset")

    coordinator.event_bus.subscribe("EVT_CONVERSATION_TURN", turns.append)
    coordinator.configure_motion(Motion())
    executor = DeferredExecutor()
    coordinator.configure_conversation(Conversation(), executor)

    assert coordinator.action_bus.execute(ActionCommand(
        "conversation", "old", trace_id="old-trace", character_id="Choppr"
    )).status == "ok"
    assert coordinator.action_bus.execute(ActionCommand("reset", source="ui")).status == "ok"

    executor.jobs[0][1](True, "", {"reply": "stale"})

    assert order == ["cancel", "reset"]
    assert turns == []


def test_full_reset_requests_all_character_domain_reset():
    calls = []
    handler = ResetHandler(
        motion=type("Motion", (), {"reset": lambda _self: calls.append("motion")})(),
        cancel_conversation=lambda: calls.append("cancel"),
        reset_domain=lambda reset_all=False: calls.append(("domain", reset_all)),
    )

    assert handler.can_handle(ActionCommand("reset_all"))
    assert handler.handle(ActionCommand("reset_all")).status == "ok"
    assert calls == ["cancel", ("domain", True), "motion"]
