from .event_action_handler import EventActionHandler


class WaveHandler(EventActionHandler):
    def __init__(self, events): super().__init__({"wave_response"}, events)
