"""
ECHOES — Centralized action binding dispatcher
解析 action 指令並協調角色動作、背景服務與 UI 狀態更新。
"""

from __future__ import annotations

import os
import re
import tempfile
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject

from action_services import MusicSelectionWorker, NewsFetchWorker
from api_client.brain_engine import ElevenLabsTTSWorker, sanitize_tts_text
from character_library import ASSETS_WEBM_DIR, MOTION_MAP
import config

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
        tts_worker_factory=ElevenLabsTTSWorker,
        motion_path_resolver=None,
        tts_enabled: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._window = window
        self._library = library
        self._workers: list[object] = []
        self._tts_worker_factory = (
            tts_worker_factory if callable(tts_worker_factory) else ElevenLabsTTSWorker
        )
        self._motion_path_resolver = motion_path_resolver
        self._tts_enabled = tts_enabled
        self._latest_tts_reply_id: str | None = None
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

    def dispatch(self, directive: str) -> bool:
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
                self._show_brain_message(display_message, has_action=False)
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
        motion_found = self._play_binding_motion(binding)
        if not motion_found:
            print(f"[ECHOES] 警告: action {action_name} 缺少對應動作，改以安全狀態執行。")
            self._window.restore_idle_video()

        getattr(self, binding.handler_name)(binding, motion_found)

        if display_message:
            try:
                self._synthesize_tts(display_message, tone=message_tone)
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

    def _show_brain_message(self, message: str, has_action: bool):
        tone = self._resolve_message_tone(message, has_action)
        timeout_ms = 4200 if tone == "warn" else 6000 if tone == "error" else 6500
        self._window.set_action_status(message, tone=tone, timeout_ms=timeout_ms)
        self._synthesize_tts(message, tone=tone)

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

    def _play_binding_motion(self, binding: ActionBinding) -> bool:
        motion_path, used_idle_fallback = self._resolve_action_motion_path(binding.motion_key)
        if not motion_path:
            return False

        should_loop = True if used_idle_fallback else not MOTION_MAP.get(binding.motion_key, {}).get("play_once", True)
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

    def _synthesize_tts(self, message: str, tone: str):
        if not self._tts_enabled or tone in {"warn", "error"}:
            return

        speech_text = sanitize_tts_text(message)
        if not speech_text:
            return

        if not callable(self._tts_worker_factory):
            print("[ECHOES] 警告: TTS worker factory 無效，已回退到 ElevenLabsTTSWorker。")
            self._tts_worker_factory = ElevenLabsTTSWorker

        reply_id = uuid4().hex
        self._latest_tts_reply_id = reply_id
        worker = self._tts_worker_factory(
            text=speech_text,
            reply_id=reply_id,
            parent=self,
        )
        self._start_worker(
            worker,
            lambda success, result_message, payload: self._on_tts_finished(
                reply_id,
                success,
                result_message,
                payload,
            ),
        )

    def _on_tts_finished(self, reply_id: str, success: bool, message: str, payload: object):
        if not success:
            print(f"[ECHOES] 提示: TTS 未播放，保留文字回覆。{message}")
            return

        if reply_id != self._latest_tts_reply_id:
            print(f"[ECHOES] 提示: 忽略過期的 TTS 音檔 {reply_id}。")
            return

        if not isinstance(payload, dict):
            print("[ECHOES] 警告: TTS payload 格式錯誤。")
            return

        audio_path = payload.get("audio_path", "")
        if not self._window.play_music(audio_path, "", update_status=False):
            print(f"[ECHOES] 警告: 無法播放 TTS 音檔: {audio_path}")

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
        self.motion_asset_calls: list[tuple[str, str, bool]] = []
        self.audio_calls: list[tuple[str, str]] = []
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

    def play_resolved_motion(self, motion_key: str, motion_path: str, loop: bool = False) -> bool:
        self.motion_calls.append(motion_key)
        self.motion_asset_calls.append((motion_key, motion_path, loop))
        self.call_order.append(("motion", motion_key))
        self._pending_play_once = not bool(loop)
        return True

    def restore_idle_video(self) -> bool:
        self.restore_idle_calls += 1
        return True

    def play_music(self, filename: str, title: str = "", update_status: bool = True) -> bool:
        self.audio_calls.append((filename, title, update_status))
        self.call_order.append(("audio", title or filename))
        return True

    def stop_music(self):
        return None

    def simulate_motion_end(self) -> bool:
        if not self._pending_play_once:
            return False
        self._pending_play_once = False
        return self.restore_idle_video()

def run_wave_response_debug_probe() -> dict[str, object]:
    directive = "[ACTION:wave_response]"
    with tempfile.TemporaryDirectory(prefix="echoes-debug-webm-") as temp_dir:
        wave_path = Path(temp_dir) / "running_forward.webm"
        idle_path = Path(temp_dir) / "Idle.webm"
        wave_path.write_bytes(b"debug")
        idle_path.write_bytes(b"debug")
        resolver = lambda motion_key: str({"wave_response": wave_path, "idle": idle_path}.get(motion_key, ""))
        window = _DebugProbeWindow()
        dispatcher = ActionDispatcher(
            window,
            library=object(),
            motion_path_resolver=resolver,
            tts_enabled=False,
        )
        dispatched = dispatcher.dispatch(directive)
        idle_restored = window.simulate_motion_end()

        return {
            "directive": directive,
            "dispatched": dispatched,
            "status_calls": window.status_calls,
            "motion_calls": window.motion_calls,
            "motion_asset_calls": window.motion_asset_calls,
            "idle_restored": idle_restored,
            "restore_idle_calls": window.restore_idle_calls,
            "ok": (
                dispatched
                and bool(window.status_calls)
                and window.status_calls[0][0] == "正在回應揮手"
                and window.motion_calls == ["wave_response"]
                and bool(window.motion_asset_calls)
                and window.motion_asset_calls[0][1].endswith("running_forward.webm")
                and idle_restored
                and window.restore_idle_calls == 1
            ),
        }


class _DebugSignal:
    def __init__(self):
        self._callbacks: list[object] = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _ImmediateTTSWorker:
    def __init__(self, text: str, reply_id: str | None = None, parent=None):
        del parent
        self._text = text
        self._reply_id = reply_id or "debug-reply"
        self.finished_signal = _DebugSignal()
        self.finished = _DebugSignal()

    def start(self):
        payload = {
            "reply_id": self._reply_id,
            "audio_path": f"/tmp/{self._reply_id}.mp3",
            "title": "ECHOES 語音 debug",
            "text": self._text,
        }
        self.finished_signal.emit(True, "語音生成完成。", payload)
        self.finished.emit()

    def deleteLater(self):
        return None


def run_tts_dispatch_debug_probe() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="echoes-debug-webm-") as temp_dir:
        listen_path = Path(temp_dir) / "listen.webm"
        idle_path = Path(temp_dir) / "Idle.webm"
        listen_path.write_bytes(b"debug")
        idle_path.write_bytes(b"debug")
        resolver = lambda motion_key: str({"listen": listen_path, "idle": idle_path}.get(motion_key, ""))
        window = _DebugProbeWindow()
        dispatcher = ActionDispatcher(
            window,
            library=object(),
            tts_worker_factory=_ImmediateTTSWorker,
            motion_path_resolver=resolver,
            tts_enabled=True,
        )
        dispatched = dispatcher.dispatch("這是一段測試語音。[ACTION:listen]")

        return {
            "dispatched": dispatched,
            "status_calls": window.status_calls,
            "motion_calls": window.motion_calls,
            "motion_asset_calls": window.motion_asset_calls,
            "audio_calls": window.audio_calls,
            "call_order": window.call_order,
            "ok": (
                dispatched
                and bool(window.audio_calls)
                and window.audio_calls[0][0].endswith(".mp3")
                and window.audio_calls[0][2] is False
                and window.motion_calls == ["listen"]
                and bool(window.motion_asset_calls)
                and window.motion_asset_calls[0][1].endswith("listen.webm")
                and len(window.call_order) >= 3
                and window.call_order[0] == ("status", "這是一段測試語音。")
                and window.call_order[1] == ("motion", "listen")
                and window.call_order[2][0] == "audio"
            ),
        }
