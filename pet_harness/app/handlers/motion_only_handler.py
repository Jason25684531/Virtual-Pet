from .event_action_handler import EventActionHandler


class MotionOnlyHandler(EventActionHandler):
    def __init__(self, events): super().__init__({"laugh", "angry", "awkward", "speechless", "listen", "idle"}, events)
