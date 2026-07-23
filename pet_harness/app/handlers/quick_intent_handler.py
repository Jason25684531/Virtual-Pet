from .event_action_handler import EventActionHandler


class QuickIntentHandler(EventActionHandler):
    def __init__(self, events): super().__init__({"cached_joke", "cached_share"}, events)
