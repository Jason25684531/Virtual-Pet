from __future__ import annotations

from typing import Any

from .action_handler import ActionHandler
from .commands import ActionCommand
from .event_bus import SimpleEventBus
from .events import AppEvent
from .ports import BackgroundExecutor
from .results import ActionResult


class ConversationHandler(ActionHandler):
    def __init__(self, conversation, executor: BackgroundExecutor, events: SimpleEventBus) -> None:
        self._conversation = conversation
        self._executor = executor
        self._events = events
        self._busy = False
        self._active_command: ActionCommand | None = None
        self._active_prepared = None

    def can_handle(self, command: ActionCommand) -> bool:
        return command.action == "conversation"

    def handle(self, command: ActionCommand) -> ActionResult:
        if self._busy and not command.metadata.get("barge_in"):
            return ActionResult("rejected", "busy")
        if self._busy:
            self.cancel_active()
        if not command.text.strip():
            return ActionResult("rejected", "empty_text")
        if not command.character_id:
            return ActionResult("rejected", "missing_character_id")
        self._busy = True
        self._active_command = command
        prepared = None
        try:
            if command.trace_id is None:
                prepared = self._conversation.prepare_turn(command.text, command.source, command.character_id)
            else:
                try:
                    prepared = self._conversation.prepare_turn(command.text, command.source, command.character_id, command.trace_id)
                except TypeError:
                    prepared = self._conversation.prepare_turn(command.text, command.source, command.character_id)
            self._active_prepared = prepared
            self._executor.submit(prepared, lambda ok, message, payload: self._completed(command, ok, message, payload))
        except Exception:
            self._busy = False
            self._active_command = None
            self._active_prepared = None
            if prepared is not None:
                prepared.release()
            raise
        return ActionResult("ok", payload={"accepted": True})

    def _completed(self, command: ActionCommand, ok: bool, message: str, payload: Any) -> None:
        if command is not self._active_command:
            return
        self._busy = False
        self._active_command = None
        self._active_prepared = None
        if not ok:
            self._events.publish(AppEvent("EVT_RUNTIME_ERROR", command.trace_id, {"message": message, "character_id": command.character_id}))
            return
        result = dict(payload or {})
        result["character_id"] = command.character_id
        self._events.publish(AppEvent("EVT_CONVERSATION_TURN", command.trace_id, result))

    def cancel_active(self) -> bool:
        if not self._busy:
            return False
        prepared = self._active_prepared
        if prepared is not None:
            prepared.cancel()
        self._busy = False
        self._active_command = None
        self._active_prepared = None
        return True


class EventActionHandler(ActionHandler):
    def __init__(self, actions: set[str], motion) -> None:
        self._actions, self._motion = actions, motion

    def can_handle(self, command: ActionCommand) -> bool:
        return command.action in self._actions

    def handle(self, command: ActionCommand) -> ActionResult:
        accepted = self._motion.dispatch_directive(f"[ACTION:{command.action}] {command.text}".strip(), trace_id=command.trace_id, allow_tts=command.allow_tts, wait_for_tts_start=command.wait_for_tts_start)
        return ActionResult("ok" if accepted else "rejected", payload={"accepted": accepted})


class MotionOnlyHandler(EventActionHandler):
    def __init__(self, motion) -> None:
        self._motion = motion

    def can_handle(self, command):
        return command.action not in {"conversation", "reset"}


class QuickIntentHandler(EventActionHandler):
    def __init__(self, motion): super().__init__({"cached_joke", "cached_share"}, motion)

    def handle(self, command):
        accepted = self._motion.trigger_cached_intent(command.action.removeprefix("cached_"), command.source)
        return ActionResult("ok" if accepted else "rejected", payload={"accepted": accepted})


class ResetHandler(ActionHandler):
    def __init__(self, motion) -> None: self._motion = motion
    def can_handle(self, command: ActionCommand) -> bool: return command.action == "reset"
    def handle(self, command: ActionCommand) -> ActionResult:
        self._motion.reset()
        return ActionResult("ok", payload={"accepted": True})


class SpeakHandler(ActionHandler):
    def __init__(self, motion) -> None: self._motion = motion
    def can_handle(self, command) -> bool: return command.action == "speak"
    def handle(self, command) -> ActionResult:
        self._motion.speak(command.text, trace_id=command.trace_id, has_action=bool(command.metadata.get("has_action")))
        return ActionResult("ok", payload={"accepted": True})
