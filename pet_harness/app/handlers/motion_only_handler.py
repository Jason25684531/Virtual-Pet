from .event_action_handler import EventActionHandler


class MotionOnlyHandler(EventActionHandler):
    def __init__(self, motion): super().__init__({"laugh", "angry", "awkward", "speechless", "listen", "idle"}, motion)

    def can_handle(self, command):
        return command.action not in {"conversation", "reset"}
