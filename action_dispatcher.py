"""
ECHOES — Centralized action binding dispatcher
解析 action 指令並協調角色動作、背景服務與 UI 狀態更新。
"""

from __future__ import annotations

import os
import queue
import re
from uuid import uuid4
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject, QTimer

from action_services import MusicSelectionWorker, NewsFetchWorker
from api_client.brain_engine import sanitize_tts_text
from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker
from character_library import ASSETS_WEBM_DIR, MOTION_MAP
import config
from interaction_trace import InteractionLatencyTracker

if TYPE_CHECKING:
    from character_library import CharacterLibrary
    from ui.transparent_window import TransparentWindow

ACTION_DIRECTIVE_PATTERN = re.compile(
    r"(?:\[\s*ACTION\s*:\s*(?P<bracket>[A-Za-z0-9_-]+)\s*\]|(?<!\w)ACTION\s*:\s*(?P<bare>[A-Za-z0-9_-]+))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ActionBinding:
    name: str
    motion_key: str
    status_label: str
    handler_name: str


class ActionDispatcher(QObject):
    """集中管理 action token 與對應行為。"""

    def __init__(
        self,
        window: "TransparentWindow",
        library: "CharacterLibrary",
        tts_worker_factory=ElevenLabsStreamingTTSWorker,
        news_worker_factory=NewsFetchWorker,
        music_worker_factory=MusicSelectionWorker,
        motion_path_resolver=None,
        tts_enabled: bool = True,
        latency_tracker: InteractionLatencyTracker | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._window = window
        self._library = library
        self._workers: list[object] = []
        self._tts_worker_factory = (
            tts_worker_factory if callable(tts_worker_factory) else ElevenLabsStreamingTTSWorker
        )
        self._news_worker_factory = (
            news_worker_factory if callable(news_worker_factory) else NewsFetchWorker
        )
        self._music_worker_factory = (
            music_worker_factory if callable(music_worker_factory) else MusicSelectionWorker
        )
        self._motion_path_resolver = motion_path_resolver
        self._tts_enabled = tts_enabled
        self._latency_tracker = latency_tracker
        self._active_tts_worker: object | None = None
        self._pending_tts_chunks: "queue.Queue[tuple[str, str, str | None]]" = queue.Queue()
        self._current_loop_action_key: str | None = None
        self._loop_action_tts_queued: bool = False
        self._loop_cleanup_timer: QTimer | None = None
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
            "wave_response": ActionBinding(
                name="wave_response",
                motion_key="wave_response",
                status_label="正在回應揮手",
                handler_name="_handle_motion_only",
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

    def dispatch(self, directive: str, trace_id: str | None = None) -> bool:
        raw_action_name, display_message = self._parse_directive(directive)
        action_name = config.canonicalize_host_action(raw_action_name)
        if raw_action_name and action_name and raw_action_name != action_name:
            print(f"[ECHOES] 提示: action alias `{raw_action_name}` 已正規化為 `{action_name}`。")

        if raw_action_name and not action_name:
            print(
                "[ECHOES] 警告: 未支援的 action: "
                f"{raw_action_name}，目前僅支援 {', '.join(config.HOST_ACTION_NAMES)}"
            )
            warn_message = display_message or f"未支援的 action: {raw_action_name}"
            if display_message:
                warn_message = f"{display_message} (未支援的 action: {raw_action_name})"
            self._window.set_action_status(warn_message, tone="warn", timeout_ms=4200)
            self._window.restore_idle_video()
            return False

        if not action_name:
            if display_message:
                self._show_brain_message(display_message, has_action=False, trace_id=trace_id)
                return True

            print(f"[ECHOES] 警告: 收到空白或無效訊息: {directive}")
            self._window.set_action_status("收到空白或無效訊息", tone="warn", timeout_ms=2800)
            return False

        binding = self._bindings.get(action_name)
        if not binding:
            print(f"[ECHOES] 警告: action `{action_name}` 尚未綁定。")
            self._window.restore_idle_video()
            return False
        message_tone = "working"
        if display_message:
            message_tone = self._resolve_message_tone(display_message, has_action=True)
            timeout_ms = 4200 if message_tone == "warn" else 6000 if message_tone == "error" else 6500
            self._window.set_action_status(display_message, tone=message_tone, timeout_ms=timeout_ms)
        else:
            self._window.set_action_status(binding.status_label, tone="working")

        print(f"[ECHOES] Action tag 命中: {action_name} -> motion `{binding.motion_key}`")
        if self._latency_tracker is not None:
            self._latency_tracker.mark_action_dispatched(trace_id, action_name)
        motion_found = self._play_binding_motion(binding)
        if not motion_found:
            print(f"[ECHOES] 警告: action {action_name} 缺少對應動作，改以安全狀態執行。")
            self._window.restore_idle_video()

        getattr(self, binding.handler_name)(binding, motion_found)

        if display_message:
            try:
                self._synthesize_tts(display_message, tone=message_tone, trace_id=trace_id)
            except Exception as exc:  # pragma: no cover - 防止 TTS 異常阻斷動作播放
                print(f"[ECHOES] 警告: TTS 背景啟動失敗，但動作已照常執行。({exc})")
        return True

    @staticmethod
    def _parse_directive(directive: str) -> tuple[str | None, str]:
        if not directive:
            return None, ""

        stripped = directive.strip()
        if not stripped:
            return None, ""

        match = ACTION_DIRECTIVE_PATTERN.search(stripped)
        message_text = ACTION_DIRECTIVE_PATTERN.sub("", stripped)
        message_text = re.sub(r"\s{2,}", " ", message_text).strip()
        if match:
            action_name = (match.group("bracket") or match.group("bare") or "").lower()
            return action_name, message_text

        normalized = stripped.lower()
        if normalized.startswith("action:"):
            return normalized.split(":", 1)[1].strip(), ""
        return None, stripped

    def _show_brain_message(self, message: str, has_action: bool, trace_id: str | None = None):
        tone = self._resolve_message_tone(message, has_action)
        timeout_ms = 4200 if tone == "warn" else 6000 if tone == "error" else 6500
        self._window.set_action_status(message, tone=tone, timeout_ms=timeout_ms)
        self._synthesize_tts(message, tone=tone, trace_id=trace_id)

    @staticmethod
    def _resolve_message_tone(message: str, has_action: bool) -> str:
        normalized = message.strip().lower()
        if normalized.startswith(("警告:", "[warn]", "warn:")) or "無法連線" in normalized or "連線已中斷" in normalized:
            return "warn"
        if normalized.startswith(("錯誤:", "[error]", "error:")):
            return "error"
        return "working" if has_action else "idle"

    def _handle_report_news(self, binding: ActionBinding, motion_found: bool):
        current_character_id = self._call_library_method("get_current_character_id")
        if current_character_id:
            panel_path = self._call_library_method("get_panel_motion_path", current_character_id, "report_news")
            if panel_path and hasattr(self._window, "play_panel_video"):
                self._window.play_panel_video(panel_path)
        worker = self._news_worker_factory(parent=self)
        self._start_worker(worker, lambda success, message, payload: self._on_news_finished(binding, motion_found, success, message, payload))

    def _handle_play_music(self, binding: ActionBinding, motion_found: bool):
        current_character_id = self._call_library_method("get_current_character_id")
        if current_character_id:
            panel_path = self._call_library_method("get_panel_motion_path", current_character_id, "play_music")
            if panel_path and hasattr(self._window, "play_panel_video"):
                self._window.play_panel_video(panel_path)
        worker = self._music_worker_factory(parent=self)
        self._start_worker(worker, lambda success, message, payload: self._on_music_finished(binding, motion_found, success, message, payload))

    def _handle_motion_only(self, binding: ActionBinding, motion_found: bool):
        if not motion_found:
            self._window.restore_idle_video()

    _LOOP_ACTION_KEYS = frozenset({"report_news", "play_music"})

    def _play_binding_motion(self, binding: ActionBinding) -> bool:
        motion_path, used_idle_fallback = self._resolve_action_motion_path(binding.motion_key)
        if not motion_path:
            self._current_loop_action_key = None
            return False

        is_loop_action = binding.motion_key in self._LOOP_ACTION_KEYS
        should_loop = True if (used_idle_fallback or is_loop_action) else not MOTION_MAP.get(binding.motion_key, {}).get("play_once", True)

        if is_loop_action:
            self._current_loop_action_key = binding.motion_key
            self._loop_action_tts_queued = False
            if hasattr(self._window, "start_motion_loop"):
                self._window.start_motion_loop(motion_path, 300)
                return True
            # fallback: native loop
            if hasattr(self._window, "play_resolved_motion"):
                return bool(self._window.play_resolved_motion(binding.motion_key, motion_path, loop=True))
            return bool(self._window.change_video(motion_path, loop=True))
        else:
            self._current_loop_action_key = None

        if hasattr(self._window, "play_resolved_motion"):
            return bool(self._window.play_resolved_motion(binding.motion_key, motion_path, loop=should_loop))

        if hasattr(self._window, "change_video"):
            return bool(self._window.change_video(motion_path, loop=should_loop))

        return bool(self._window.play_action_motion(binding.motion_key))

    def _resolve_action_motion_path(self, motion_key: str) -> tuple[str | None, bool]:
        motion_path = self._find_motion_path(motion_key)
        if motion_path:
            return motion_path, False

        idle_path = self._find_motion_path("idle")
        print(f"[ECHOES WARNING] 找不到動作檔案: {motion_key}, 退回 Idle")
        return idle_path, True

    def _find_motion_path(self, motion_key: str) -> str | None:
        resolver_path = self._resolve_via_injected_resolver(motion_key)
        if resolver_path:
            return resolver_path

        current_character_id = self._call_library_method("get_current_character_id")
        candidates: list[str | os.PathLike[str] | None] = []
        if current_character_id:
            candidates.append(
                self._call_library_method("get_action_motion_path", current_character_id, motion_key)
            )
            candidates.append(
                self._call_library_method("get_motion_path", current_character_id, motion_key)
            )

        demo_path = self._build_demo_motion_path(motion_key)
        if demo_path:
            candidates.append(demo_path)

        if motion_key == "idle":
            candidates.append(os.path.join(str(ASSETS_WEBM_DIR), "Idle.webm"))
            candidates.append(os.path.join(str(ASSETS_WEBM_DIR), "idle.webm"))

        for candidate in candidates:
            resolved = self._resolve_existing_webm_path(candidate)
            if resolved:
                return resolved
        return None

    def _resolve_via_injected_resolver(self, motion_key: str) -> str | None:
        if not callable(self._motion_path_resolver):
            return None

        try:
            candidate = self._motion_path_resolver(motion_key)
        except Exception as exc:
            print(f"[ECHOES] 警告: motion_path_resolver 發生異常: {exc}")
            return None
        return self._resolve_existing_webm_path(candidate)

    def _build_demo_motion_path(self, motion_key: str) -> str | None:
        mapping = getattr(self._window, "DEMO_MOTION_MAPPING", None)
        animations_dir = getattr(self._window, "DEMO_ANIMATIONS_DIR", None)
        if not isinstance(mapping, dict) or not animations_dir:
            return None

        demo_filename = mapping.get(motion_key)
        if not demo_filename:
            return None
        return os.path.join(os.fspath(animations_dir), demo_filename)

    def _call_library_method(self, method_name: str, *args):
        method = getattr(self._library, method_name, None)
        if not callable(method):
            return None
        try:
            return method(*args)
        except Exception as exc:
            print(f"[ECHOES] 警告: CharacterLibrary.{method_name} 呼叫失敗: {exc}")
            return None

    @staticmethod
    def _resolve_existing_webm_path(candidate: str | os.PathLike[str] | None) -> str | None:
        if not candidate:
            return None

        absolute_path = os.path.abspath(os.path.normpath(os.fspath(candidate)))
        if not absolute_path.lower().endswith(".webm"):
            return None
        if not os.path.exists(absolute_path):
            return None
        return absolute_path

    def _start_worker(self, worker, callback):
        self._workers.append(worker)

        def handle_result(success: bool, message: str, payload: object, current_worker=worker):
            try:
                callback(success, message, payload)
            except Exception as exc:
                print(f"[ECHOES] 警告: worker callback 發生異常: {exc}")

        def on_thread_finished(current_worker=worker):
            # QThread.finished 在執行緒底層完全終止後才觸發，此時移除引用才安全。
            if current_worker in self._workers:
                self._workers.remove(current_worker)
            if hasattr(current_worker, "deleteLater"):
                current_worker.deleteLater()

        worker.finished_signal.connect(handle_result)
        worker.finished.connect(on_thread_finished)
        worker.start()

    def _synthesize_tts(self, message: str, tone: str, trace_id: str | None = None):
        if not self._tts_enabled or tone in {"warn", "error"}:
            return

        speech_text = sanitize_tts_text(message)
        if not speech_text:
            return

        if not callable(self._tts_worker_factory):
            print("[ECHOES] 警告: TTS worker factory 無效，已回退到 ElevenLabsStreamingTTSWorker。")
            self._tts_worker_factory = ElevenLabsStreamingTTSWorker

        reply_id = uuid4().hex
        self._pending_tts_chunks.put((reply_id, speech_text, trace_id))
        if self._current_loop_action_key is not None:
            self._loop_action_tts_queued = True
            if self._loop_cleanup_timer is not None:
                self._loop_cleanup_timer.stop()
                self._loop_cleanup_timer = None
        if self._latency_tracker is not None:
            self._latency_tracker.mark_tts_enqueued(trace_id, reply_id, speech_text)
        self._start_next_tts_worker()

    def _start_next_tts_worker(self):
        if self._active_tts_worker is not None or self._pending_tts_chunks.empty():
            if (self._active_tts_worker is None
                    and self._pending_tts_chunks.empty()
                    and self._loop_action_tts_queued
                    and self._current_loop_action_key is not None):
                self._finish_loop_action()
            return

        reply_id, speech_text, trace_id = self._pending_tts_chunks.get_nowait()
        worker = self._tts_worker_factory(
            text=speech_text,
            reply_id=reply_id,
            trace_id=trace_id,
            parent=self,
        )
        self._active_tts_worker = worker
        self._workers.append(worker)

        def handle_result(success: bool, result_message: str, payload: object, current_reply_id=reply_id):
            self._on_tts_finished(current_reply_id, success, result_message, payload)

        def handle_progress(event_name: str, payload: object):
            self._on_tts_progress(event_name, payload)

        def on_thread_finished(current_worker=worker):
            if current_worker in self._workers:
                self._workers.remove(current_worker)
            if self._active_tts_worker is current_worker:
                self._active_tts_worker = None
            if hasattr(current_worker, "deleteLater"):
                current_worker.deleteLater()
            self._start_next_tts_worker()

        worker.finished_signal.connect(handle_result)
        if hasattr(worker, "progress_signal"):
            worker.progress_signal.connect(handle_progress)
        worker.finished.connect(on_thread_finished)
        worker.start()

    def _on_tts_progress(self, event_name: str, payload: object):
        if event_name != "stream_started" or not isinstance(payload, dict):
            return
        if self._latency_tracker is not None:
            self._latency_tracker.mark_tts_stream_started(
                payload.get("trace_id"),
                str(payload.get("reply_id", "")),
                int(payload.get("bytes_forwarded", 0) or 0),
            )

    def _on_tts_finished(self, reply_id: str, success: bool, message: str, payload: object):
        trace_id = payload.get("trace_id") if isinstance(payload, dict) else None
        if self._latency_tracker is not None:
            self._latency_tracker.mark_tts_finished(trace_id, reply_id, success, message)
        if not success:
            print(f"[ECHOES] 提示: 串流 TTS 未播放，保留文字回覆。{message}")
            return

        print(f"[ECHOES] 提示: 串流語音片段播放完成。{message}")

    def shutdown(self, wait_ms: int = 5000):
        while not self._pending_tts_chunks.empty():
            try:
                self._pending_tts_chunks.get_nowait()
            except queue.Empty:
                break

        workers = list(self._workers)
        active_worker = self._active_tts_worker
        if active_worker is not None and active_worker not in workers:
            workers.append(active_worker)

        for worker in workers:
            try:
                if hasattr(worker, "quit"):
                    worker.quit()
            except Exception:
                pass

            is_running = getattr(worker, "isRunning", None)
            wait = getattr(worker, "wait", None)
            terminate = getattr(worker, "terminate", None)
            if callable(is_running) and callable(wait) and is_running():
                if not wait(wait_ms) and callable(terminate):
                    try:
                        terminate()
                        wait(1000)
                    except Exception:
                        pass

        self._active_tts_worker = None
        self._workers = []

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
            self._schedule_loop_cleanup(8000)
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
        has_audio = False
        if success and isinstance(payload, dict):
            if self._window.play_music(payload.get("path", ""), payload.get("title", "")):
                self._window.set_action_status(f"正在播放: {payload.get('title', message)}", tone="music")
                has_audio = True

        if not has_audio:
            self._window.stop_music()
            print(f"[ECHOES] 提示: 無可播放音樂（{message}），動畫繼續顯示。")
            self._window.set_action_status("音樂播放中", tone="music")

        self._schedule_loop_cleanup(8000)

    def _schedule_loop_cleanup(self, delay_ms: int = 8000):
        if self._loop_cleanup_timer is not None:
            self._loop_cleanup_timer.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._finish_loop_action)
        timer.start(delay_ms)
        self._loop_cleanup_timer = timer

    def _finish_loop_action(self):
        if self._current_loop_action_key is None:
            return
        print(f"[ECHOES] loop action '{self._current_loop_action_key}' 完成，清理動畫")
        if self._loop_cleanup_timer is not None:
            self._loop_cleanup_timer.stop()
            self._loop_cleanup_timer = None
        self._current_loop_action_key = None
        self._loop_action_tts_queued = False
        if hasattr(self._window, "stop_motion_loop"):
            self._window.stop_motion_loop()
        if hasattr(self._window, "clear_panel_video"):
            self._window.clear_panel_video()
        self._window.restore_idle_video()

    def _handle_failure(self, binding: ActionBinding, motion_found: bool, message: str):
        print(f"[ECHOES] 警告: action {binding.name} 執行失敗: {message}")
        self._finish_loop_action()
        self._window.set_action_status(message, tone="error", timeout_ms=6000)
        self._window.restore_idle_video()
