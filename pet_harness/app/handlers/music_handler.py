from .event_action_handler import EventActionHandler


class MusicHandler(EventActionHandler):
    def __init__(self, motion): super().__init__({"play_music"}, motion)
