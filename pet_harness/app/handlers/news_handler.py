from .event_action_handler import EventActionHandler


class NewsHandler(EventActionHandler):
    def __init__(self, motion): super().__init__({"report_news"}, motion)
