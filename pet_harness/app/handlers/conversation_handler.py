from __future__ import annotations

from typing import Any

from ..action_handler import ActionHandler
from ..commands import ActionCommand
from ..event_bus import EventBus
from ..events import AppEvent
from ..ports.background_executor import BackgroundExecutor
from ..ports.conversation_port import ConversationPort
from ..results import ActionResult


class ConversationHandler(ActionHandler):
    def __init__(self, conversation: ConversationPort, executor: BackgroundExecutor, events: EventBus) -> None:
        self._conversation = conversation
        self._executor = executor
        self._events = events
        self._busy = False

    def can_handle(self, command: ActionCommand) -> bool:
        return command.action == "conversation"

    def handle(self, command: ActionCommand) -> ActionResult:
        if self._busy:
            return ActionResult("rejected", "busy")
        if not command.text.strip():
            return ActionResult("rejected", "empty_text")
        if not command.character_id:
            return ActionResult("rejected", "missing_character_id")
        self._busy = True
        self._executor.submit(
            self._conversation.prepare_turn(command.text, command.source, command.character_id),
            lambda ok, message, payload: self._completed(command, ok, message, payload),
        )
        return ActionResult("ok", payload={"accepted": True})

    def _completed(self, command: ActionCommand, ok: bool, message: str, payload: Any) -> None:
        self._busy = False
        if not ok:
            self._events.publish(AppEvent("EVT_RUNTIME_ERROR", command.trace_id, {
                "message": message, "character_id": command.character_id,
            }))
            return
        # 動畫/TTS/XP 由 EVT_CONVERSATION_TURN 的消費者（consume_interaction_result）
        # 統一觸發；不另發 motion/tts/xp 事件，避免未來有人訂閱後雙重播放。
        result = dict(payload or {})
        result["character_id"] = command.character_id
        self._events.publish(AppEvent("EVT_CONVERSATION_TURN", command.trace_id, result))
