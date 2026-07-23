from .event_action_handler import EventActionHandler


class MusicHandler(EventActionHandler):
    def __init__(self, events): super().__init__({"play_music"}, events)
