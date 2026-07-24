from ..action_handler import ActionHandler
from ..results import ActionResult


class SpeakHandler(ActionHandler):
    def __init__(self, motion) -> None: self._motion = motion
    def can_handle(self, command) -> bool: return command.action == "speak"
    def handle(self, command) -> ActionResult:
        self._motion.speak(command.text, trace_id=command.trace_id, has_action=bool(command.metadata.get("has_action")))
        return ActionResult("ok", payload={"accepted": True})
