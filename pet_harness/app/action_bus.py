from .action_handler import ActionHandler
from .commands import ActionCommand
from .event_bus import EventBus
from .events import AppEvent
from .results import ActionResult


class ActionBus:
    def __init__(self, event_bus: EventBus, handlers: tuple[ActionHandler, ...] = ()) -> None:
        self._events = event_bus
        self._handlers = list(handlers)

    def register(self, handler: ActionHandler) -> None:
        self._handlers.append(handler)

    def execute(self, command: ActionCommand) -> ActionResult:
        handler = next((item for item in self._handlers if item.can_handle(command)), None)
        if handler is None:
            return ActionResult("rejected", "unknown_action")
        try:
            return handler.handle(command)
        except Exception as exc:  # one action cannot break the UI event loop
            payload = {"message": str(exc)}
            if command.character_id:
                payload["character_id"] = command.character_id
            self._events.publish(AppEvent("EVT_RUNTIME_ERROR", command.trace_id, payload))
            return ActionResult("failed", str(exc))
