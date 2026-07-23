from ..action_handler import ActionHandler
from ..commands import ActionCommand
from ..event_bus import EventBus
from ..events import AppEvent
from ..results import ActionResult


class ResetHandler(ActionHandler):
    def __init__(self, events: EventBus) -> None: self._events = events
    def can_handle(self, command: ActionCommand) -> bool: return command.action == "reset"
    def handle(self, command: ActionCommand) -> ActionResult:
        self._events.publish(AppEvent("EVT_RESET_REQUESTED", command.trace_id, {}))
        return ActionResult("ok", payload={"accepted": True})
