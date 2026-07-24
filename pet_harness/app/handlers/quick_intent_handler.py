from .event_action_handler import EventActionHandler


class QuickIntentHandler(EventActionHandler):
    def __init__(self, motion): super().__init__({"cached_joke", "cached_share"}, motion)

    def handle(self, command):
        accepted = self._motion.trigger_cached_intent(command.action.removeprefix("cached_"), command.source)
        from ..results import ActionResult
        return ActionResult("ok" if accepted else "rejected", payload={"accepted": accepted})
