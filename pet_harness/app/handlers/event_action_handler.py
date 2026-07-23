from ..action_handler import ActionHandler
from ..commands import ActionCommand
from ..event_bus import EventBus
from ..events import AppEvent
from ..results import ActionResult


class EventActionHandler(ActionHandler):
    def __init__(self, actions: set[str], events: EventBus) -> None:
        self._actions, self._events = actions, events

    def can_handle(self, command: ActionCommand) -> bool:
        return command.action in self._actions

    def handle(self, command: ActionCommand) -> ActionResult:
        self._events.publish(AppEvent("EVT_ACTION_REQUESTED", command.trace_id, {
            "action": command.action, "text": command.text, "allow_tts": command.allow_tts,
            "wait_for_tts_start": command.wait_for_tts_start, "source": command.source,
            "metadata": command.metadata,
        }))
        return ActionResult("ok", payload={"accepted": True})
