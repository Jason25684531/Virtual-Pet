from ..action_handler import ActionHandler
from ..commands import ActionCommand
from ..ports.motion_port import MotionPort
from ..results import ActionResult


class ResetHandler(ActionHandler):
    def __init__(self, motion: MotionPort) -> None: self._motion = motion
    def can_handle(self, command: ActionCommand) -> bool: return command.action == "reset"
    def handle(self, command: ActionCommand) -> ActionResult:
        self._motion.reset()
        return ActionResult("ok", payload={"accepted": True})
