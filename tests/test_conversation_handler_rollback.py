from pet_harness.app.action_bus import ActionBus
from pet_harness.app.commands import ActionCommand
from pet_harness.app.event_bus import SimpleEventBus
from pet_harness.app.handlers import ConversationHandler
from pet_harness.app.ports import PreparedTurn


class _Conversation:
    def __init__(self, fail_prepare=False):
        self.fail_prepare = fail_prepare
        self.releases = []

    def prepare_turn(self, text, source, character_id):
        if self.fail_prepare:
            raise RuntimeError("prepare failed")
        return PreparedTurn(lambda: {"reply": text}, lambda: self.releases.append(character_id))


class _Executor:
    def __init__(self, fail_submit=False):
        self.fail_submit = fail_submit

    def submit(self, job, on_done):
        if self.fail_submit:
            raise RuntimeError("submit failed")
        on_done(True, "", job())


def test_prepare_failure_clears_busy_and_uses_action_bus_error_event():
    events, conversation = [], _Conversation(fail_prepare=True)
    bus = ActionBus(SimpleEventBus())
    bus._events.subscribe("EVT_RUNTIME_ERROR", events.append)
    handler = ConversationHandler(conversation, _Executor(), bus._events)
    bus.register(handler)

    result = bus.execute(ActionCommand("conversation", "hello", character_id="Choppr"))

    assert result.status == "failed"
    assert handler._busy is False
    assert events[0].payload == {"message": "prepare failed", "character_id": "Choppr"}


def test_submit_failure_releases_lease_once_and_later_turn_is_accepted():
    events, conversation, executor = [], _Conversation(), _Executor(fail_submit=True)
    bus = ActionBus(SimpleEventBus())
    bus._events.subscribe("EVT_RUNTIME_ERROR", events.append)
    handler = ConversationHandler(conversation, executor, bus._events)
    bus.register(handler)
    command = ActionCommand("conversation", "hello", character_id="Choppr")

    assert bus.execute(command).status == "failed"
    assert handler._busy is False
    assert conversation.releases == ["Choppr"]
    executor.fail_submit = False

    assert bus.execute(command).status == "ok"
    assert conversation.releases == ["Choppr", "Choppr"]
    assert events[0].payload["character_id"] == "Choppr"
