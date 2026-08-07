from ..action_handler import ActionHandler
from ..commands import ActionCommand
from ..ports.motion_port import MotionPort
from ..results import ActionResult


class EventActionHandler(ActionHandler):
    def __init__(self, actions: set[str], motion: MotionPort) -> None:
        self._actions, self._motion = actions, motion

    def can_handle(self, command: ActionCommand) -> bool:
        return command.action in self._actions

    def handle(self, command: ActionCommand) -> ActionResult:
        accepted = self._motion.dispatch_directive(
            f"[ACTION:{command.action}] {command.text}".strip(),
            trace_id=command.trace_id,
            allow_tts=command.allow_tts,
            wait_for_tts_start=command.wait_for_tts_start,
        )
        return ActionResult("ok" if accepted else "rejected", payload={"accepted": accepted})
