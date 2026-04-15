"""
ECHOES — Centralized action binding dispatcher
解析 action 指令並協調角色動作、背景服務與 UI 狀態更新。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject

from action_services import MusicSelectionWorker, NewsFetchWorker

if TYPE_CHECKING:
    from character_library import CharacterLibrary
    from ui.transparent_window import TransparentWindow


ACTION_TOKEN_PATTERN = re.compile(r"\[ACTION:(?P<name>[A-Za-z0-9_-]+)\]")


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
        }

    def dispatch(self, directive: str) -> bool:
        action_name = self._parse_action_name(directive)
        if not action_name:
            print(f"[ECHOES] 警告: 無法解析 action 指令: {directive}")
            self._window.set_action_status("無法解析 action 指令", tone="warn", timeout_ms=2800)
            return False

        binding = self._bindings.get(action_name)
        if not binding:
            print(f"[ECHOES] 警告: 未支援的 action: {action_name}")
            self._window.set_action_status(f"未支援的 action: {action_name}", tone="warn", timeout_ms=3200)
            self._window.restore_idle_video()
            return False

        self._window.set_action_status(binding.status_label, tone="working")
        motion_found = self._window.play_action_motion(binding.motion_key)
        if not motion_found:
            print(f"[ECHOES] 警告: action {action_name} 缺少對應動作，改以安全狀態執行。")
            self._window.restore_idle_video()

        getattr(self, binding.handler_name)(binding, motion_found)
        return True

    @staticmethod
    def _parse_action_name(directive: str) -> str | None:
        if not directive:
            return None

        match = ACTION_TOKEN_PATTERN.search(directive.strip())
        if match:
            return match.group("name").lower()

        normalized = directive.strip().lower()
        if normalized.startswith("action:"):
            return normalized.split(":", 1)[1]
        return normalized or None

    def _handle_report_news(self, binding: ActionBinding, motion_found: bool):
        worker = NewsFetchWorker(parent=self)
        self._start_worker(worker, lambda success, message, payload: self._on_news_finished(binding, motion_found, success, message, payload))

    def _handle_play_music(self, binding: ActionBinding, motion_found: bool):
        worker = MusicSelectionWorker(parent=self)
        self._start_worker(worker, lambda success, message, payload: self._on_music_finished(binding, motion_found, success, message, payload))

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