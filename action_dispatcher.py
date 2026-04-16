"""
ECHOES — Centralized action binding dispatcher
解析 action 指令並協調角色動作、背景服務與 UI 狀態更新。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject

from action_services import MusicSelectionWorker, NewsFetchWorker
from character_library import MOTION_MAP

if TYPE_CHECKING:
    from character_library import CharacterLibrary
    from ui.transparent_window import TransparentWindow


ACTION_TOKEN_PATTERN = re.compile(r"\[ACTION:(?P<name>[A-Za-z0-9_-]+)\]", re.IGNORECASE)


@dataclass(frozen=True)
class ActionBinding:
    name: str
    motion_key: str
    status_label: str
    handler_name: str


class ActionDispatcher(QObject):
    """集中管理 action token 與對應行為。"""

    def __init__(self, window: "TransparentWindow", library: "CharacterLibrary", parent=None):
        super().__init__(parent)
        self._window = window
        self._library = library
        self._workers: list[object] = []
        self._bindings = {
            "report_news": ActionBinding(
                name="report_news",
                motion_key="report_news",
                status_label="正在整理新聞",
                handler_name="_handle_report_news",
            ),
            "play_music": ActionBinding(
                name="play_music",
                motion_key="play_music",
                status_label="正在挑選音樂",
                handler_name="_handle_play_music",
            ),
            "laugh": ActionBinding(
                name="laugh",
                motion_key="laugh",
                status_label="正在開心大笑",
                handler_name="_handle_motion_only",
            ),
            "angry": ActionBinding(
                name="angry",
                motion_key="angry",
                status_label="正在表達反對",
                handler_name="_handle_motion_only",
            ),
            "awkward": ActionBinding(
                name="awkward",
                motion_key="awkward",
                status_label="有點尷尬",
                handler_name="_handle_motion_only",
            ),
            "speechless": ActionBinding(
                name="speechless",
                motion_key="speechless",
                status_label="目前無言中",
                handler_name="_handle_motion_only",
            ),
            "listen": ActionBinding(
                name="listen",
                motion_key="listen",
                status_label="正在專心聆聽",
                handler_name="_handle_motion_only",
            ),
            "idle": ActionBinding(
                name="idle",
                motion_key="idle",
                status_label="回到待命狀態",
                handler_name="_handle_motion_only",
            ),
        }

    def dispatch(self, directive: str) -> bool:
        action_name, display_message = self._parse_directive(directive)
        if not action_name:
            if display_message:
                self._show_brain_message(display_message, has_action=False)
                return True

            print(f"[ECHOES] 警告: 收到空白或無效訊息: {directive}")
            self._window.set_action_status("收到空白或無效訊息", tone="warn", timeout_ms=2800)
            return False

        binding = self._bindings.get(action_name)
        if not binding:
            print(f"[ECHOES] 警告: 未支援的 action: {action_name}")
            warn_message = display_message or f"未支援的 action: {action_name}"
            if display_message:
                warn_message = f"{display_message} (未支援的 action: {action_name})"
            self._window.set_action_status(warn_message, tone="warn", timeout_ms=4200)
            self._window.restore_idle_video()
            return False

        if display_message:
            self._show_brain_message(display_message, has_action=True)
        else:
            self._window.set_action_status(binding.status_label, tone="working")

        motion_found = self._window.play_action_motion(binding.motion_key)
        if not motion_found:
            print(f"[ECHOES] 警告: action {action_name} 缺少對應動作，改以安全狀態執行。")
            self._window.restore_idle_video()

        getattr(self, binding.handler_name)(binding, motion_found)
        return True

    @staticmethod
    def _parse_directive(directive: str) -> tuple[str | None, str]:
        if not directive:
            return None, ""

        stripped = directive.strip()
        if not stripped:
            return None, ""

        match = ACTION_TOKEN_PATTERN.search(stripped)
        message_text = ACTION_TOKEN_PATTERN.sub("", stripped)
        message_text = re.sub(r"\s{2,}", " ", message_text).strip()
        if match:
            return match.group("name").lower(), message_text

        normalized = stripped.lower()
        if normalized.startswith("action:"):
            return normalized.split(":", 1)[1].strip(), ""
        return None, stripped

    def _show_brain_message(self, message: str, has_action: bool):
        tone = self._resolve_message_tone(message, has_action)
        timeout_ms = 4200 if tone == "warn" else 6000 if tone == "error" else 6500
        self._window.set_action_status(message, tone=tone, timeout_ms=timeout_ms)

    @staticmethod
    def _resolve_message_tone(message: str, has_action: bool) -> str:
        normalized = message.strip().lower()
        if normalized.startswith(("警告:", "[warn]", "warn:")) or "無法連線" in normalized or "連線已中斷" in normalized:
            return "warn"
        if normalized.startswith(("錯誤:", "[error]", "error:")):
            return "error"
        return "working" if has_action else "idle"

    def _handle_report_news(self, binding: ActionBinding, motion_found: bool):
        worker = NewsFetchWorker(parent=self)
        self._start_worker(worker, lambda success, message, payload: self._on_news_finished(binding, motion_found, success, message, payload))

    def _handle_play_music(self, binding: ActionBinding, motion_found: bool):
        worker = MusicSelectionWorker(parent=self)
        self._start_worker(worker, lambda success, message, payload: self._on_music_finished(binding, motion_found, success, message, payload))

    def _handle_motion_only(self, binding: ActionBinding, motion_found: bool):
        if not motion_found:
            self._window.restore_idle_video()

    def _start_worker(self, worker, callback):
        self._workers.append(worker)

        def handle_finished(success: bool, message: str, payload: object, current_worker=worker):
            try:
                callback(success, message, payload)
            finally:
                if current_worker in self._workers:
                    self._workers.remove(current_worker)
                current_worker.deleteLater()

        worker.finished_signal.connect(handle_finished)
        worker.start()

    def _on_news_finished(
        self,
        binding: ActionBinding,
        motion_found: bool,
        success: bool,
        message: str,
        payload: object,
    ):
        if success:
            headline = payload.get("headline") if isinstance(payload, dict) else message
            self._window.set_action_status(f"新聞焦點: {headline}", tone="news", timeout_ms=9000)
            return

        self._handle_failure(binding, motion_found, message)

    def _on_music_finished(
        self,
        binding: ActionBinding,
        motion_found: bool,
        success: bool,
        message: str,
        payload: object,
    ):
        if success and isinstance(payload, dict):
            if self._window.play_music(payload.get("path", ""), payload.get("title", "")):
                self._window.set_action_status(f"正在播放: {payload.get('title', message)}", tone="music")
                return

        self._window.stop_music()
        self._handle_failure(binding, motion_found, message)

    def _handle_failure(self, binding: ActionBinding, motion_found: bool, message: str):
        print(f"[ECHOES] 警告: action {binding.name} 執行失敗: {message}")
        self._window.set_action_status(message, tone="error", timeout_ms=6000)
        self._window.restore_idle_video()


class _DebugProbeWindow:
    def __init__(self):
        self.status_calls: list[tuple[str, str, int]] = []
        self.motion_calls: list[str] = []
        self.restore_idle_calls = 0
        self._pending_play_once = False
        self.call_order: list[tuple[str, str]] = []

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self.status_calls.append((message, tone, timeout_ms))
        self.call_order.append(("status", message))

    def play_action_motion(self, motion_key: str) -> bool:
        self.motion_calls.append(motion_key)
        self.call_order.append(("motion", motion_key))
        self._pending_play_once = bool(MOTION_MAP.get(motion_key, {}).get("play_once", False))
        return True

    def restore_idle_video(self) -> bool:
        self.restore_idle_calls += 1
        return True

    def play_music(self, filename: str, title: str = "") -> bool:
        return True

    def stop_music(self):
        return None

    def simulate_motion_end(self) -> bool:
        if not self._pending_play_once:
            return False
        self._pending_play_once = False
        return self.restore_idle_video()


def run_mixed_message_debug_probe() -> dict[str, object]:
    directive = "[ACTION:laugh] 哈哈，Nolan 你看這個技術架構！"
    window = _DebugProbeWindow()
    dispatcher = ActionDispatcher(window, library=object())
    dispatched = dispatcher.dispatch(directive)
    idle_restored = window.simulate_motion_end()

    return {
        "directive": directive,
        "dispatched": dispatched,
        "status_calls": window.status_calls,
        "motion_calls": window.motion_calls,
        "call_order": window.call_order,
        "idle_restored": idle_restored,
        "restore_idle_calls": window.restore_idle_calls,
        "ok": (
            dispatched
            and bool(window.status_calls)
            and window.status_calls[0][0] == "哈哈，Nolan 你看這個技術架構！"
            and window.motion_calls == ["laugh"]
            and window.call_order[:2] == [("status", "哈哈，Nolan 你看這個技術架構！"), ("motion", "laugh")]
            and idle_restored
            and window.restore_idle_calls == 1
        ),
    }


def run_backend_payload_e2e_probe() -> dict[str, object]:
    from api_client.vm_connector import VMConnector

    backend_payload = {"payload": {"action": "laugh", "text": "連線成功！Nolan，我們準備好了。"}}
    directive = VMConnector._normalize_incoming_message(backend_payload)
    window = _DebugProbeWindow()
    dispatcher = ActionDispatcher(window, library=object())
    dispatched = dispatcher.dispatch(directive or "")
    idle_restored = window.simulate_motion_end()

    return {
        "backend_payload": json.dumps(backend_payload, ensure_ascii=False),
        "directive": directive,
        "dispatched": dispatched,
        "status_calls": window.status_calls,
        "motion_calls": window.motion_calls,
        "call_order": window.call_order,
        "idle_restored": idle_restored,
        "restore_idle_calls": window.restore_idle_calls,
        "ok": (
            directive == "[ACTION:laugh] 連線成功！Nolan，我們準備好了。"
            and dispatched
            and bool(window.status_calls)
            and window.status_calls[0][0] == "連線成功！Nolan，我們準備好了。"
            and window.motion_calls == ["laugh"]
            and window.call_order[:2] == [("status", "連線成功！Nolan，我們準備好了。"), ("motion", "laugh")]
            and idle_restored
            and window.restore_idle_calls == 1
        ),
    }