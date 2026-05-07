from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from PyQt5.QtCore import QCoreApplication, QUrl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from action_dispatcher import (
    ActionDispatcher,
)
from api_client.brain_engine import StreamedReplyParser
from interaction_trace import InteractionLatencyTracker


class _NoopLibrary:
    def get_current_character_id(self):
        return None


class _DispatchProbeWindow:
    DEMO_MOTION_MAPPING = {
        "idle": "Idle.webm",
        "report_news": "report_news.webm",
        "wave_response": "running_forward.webm",
    }

    def __init__(self, demo_dir: str):
        self.DEMO_ANIMATIONS_DIR = demo_dir
        self.status_calls: list[tuple[str, str, int]] = []
        self.played_assets: list[tuple[str, str, bool]] = []
        self.audio_calls: list[tuple[str, str, bool]] = []
        self.play_music_result = True
        self.restore_idle_calls = 0

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self.status_calls.append((message, tone, timeout_ms))

    def play_resolved_motion(self, motion_key: str, motion_path: str, loop: bool = False) -> bool:
        self.played_assets.append((motion_key, motion_path, loop))
        return True

    def restore_idle_video(self) -> bool:
        self.restore_idle_calls += 1
        return True

    def play_music(self, filename: str, title: str = "", update_status: bool = True) -> bool:
        self.audio_calls.append((filename, title, update_status))
        return self.play_music_result

    def stop_music(self):
        return None


class _LoopActionProbeWindow(_DispatchProbeWindow):
    def __init__(self, demo_dir: str):
        super().__init__(demo_dir)
        self.motion_loop_calls: list[tuple[str, int]] = []
        self.stop_motion_loop_calls = 0
        self.panel_calls: list[tuple[str, bool, bool]] = []
        self.panel_mute_updates: list[bool] = []
        self.clear_panel_calls = 0

    def start_motion_loop(self, path: str, interval_ms: int = 1000):
        self.motion_loop_calls.append((path, interval_ms))

    def stop_motion_loop(self):
        self.stop_motion_loop_calls += 1

    def play_panel_video(self, path: str, muted: bool = True, loop: bool = False):
        self.panel_calls.append((path, muted, loop))

    def set_panel_video_muted(self, muted: bool):
        self.panel_mute_updates.append(bool(muted))

    def clear_panel_video(self):
        self.clear_panel_calls += 1


class _PanelLibrary(_NoopLibrary):
    def __init__(self, panel_paths: dict[str, str]):
        self._panel_paths = dict(panel_paths)

    def get_current_character_id(self):
        return "probe-character"

    def get_panel_motion_path(self, _character_id: str, action_name: str):
        return self._panel_paths.get(action_name)


class _DebugProbeWindow:
    def __init__(self):
        self.status_calls: list[tuple[str, str, int]] = []
        self.motion_calls: list[str] = []
        self.motion_asset_calls: list[tuple[str, str, bool]] = []
        self.audio_calls: list[tuple[str, str, bool]] = []
        self.restore_idle_calls = 0
        self._pending_play_once = False
        self.call_order: list[tuple[str, str]] = []

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self.status_calls.append((message, tone, timeout_ms))
        self.call_order.append(("status", message))

    def play_action_motion(self, motion_key: str) -> bool:
        self.motion_calls.append(motion_key)
        self.call_order.append(("motion", motion_key))
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


class _FakePage:
    def __init__(self):
        self.scripts: list[str] = []

    def runJavaScript(self, script: str):
        self.scripts.append(script)


class _FakeWebView:
    def __init__(self):
        self._page = _FakePage()

    def page(self):
        return self._page


class _ChangeVideoHarness:
    RAW_JAVASCRIPT_MARKER = "__raw_javascript__"

    def __init__(self):
        self._webview_ready = True
        self._pending_javascript_calls: list[tuple[str, tuple[object, ...]]] = []
        self.web_view = _FakeWebView()

    def change_video(self, filename, loop=True) -> bool:
        absolute_path = self._resolve_media_path(filename)
        if not absolute_path or not os.path.exists(absolute_path):
            print(f"[ECHOES ERROR] WebM 檔案不存在: {absolute_path or filename}")
            return False

        source_url = QUrl.fromLocalFile(absolute_path).toString(QUrl.FullyEncoded)
        print(f"[ECHOES] 送出影片 URL: {source_url}")
        if loop:
            self._run_javascript("setIdleVideo", source_url)
            return True

        safe_url = self._escape_javascript_single_quoted_string(source_url)
        self._run_raw_javascript(
            "if (window.playTemporaryVideo) { "
            f"window.playTemporaryVideo('{safe_url}');"
            " } else { console.error('[ECHOES] playTemporaryVideo bridge 不存在'); }"
        )
        return True

    def _run_raw_javascript(self, script: str):
        if not self._webview_ready:
            self._pending_javascript_calls.append((self.RAW_JAVASCRIPT_MARKER, (script,)))
            return

        self.web_view.page().runJavaScript(script)

    def _run_javascript(self, function_name: str, *args):
        if not self._webview_ready:
            self._pending_javascript_calls.append((function_name, args))
            return

        if function_name == self.RAW_JAVASCRIPT_MARKER:
            script = str(args[0]) if args else ""
            self.web_view.page().runJavaScript(script)
            return

        self.web_view.page().runJavaScript(self._build_javascript_bridge_call(function_name, *args))

    @staticmethod
    def _build_javascript_bridge_call(function_name: str, *args) -> str:
        js_function_name = json.dumps(function_name)
        js_args = ", ".join(json.dumps(arg) for arg in args)
        return (
            "(function(){"
            f"var fn = window[{js_function_name}];"
            f"if (typeof fn !== 'function') {{ console.warn('[ECHOES] JS bridge 缺少函式:', {js_function_name}); return false; }}"
            f"fn({js_args});"
            "return true;"
            "})();"
        )

    @staticmethod
    def _escape_javascript_single_quoted_string(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\r", "\\r")
            .replace("\n", "\\n")
        )

    def _resolve_media_path(self, filename: str) -> str | None:
        return os.path.abspath(os.path.normpath(filename))

    @staticmethod
    def _build_media_source_url(absolute_path: str) -> str:
        source_url = QUrl.fromLocalFile(absolute_path).toString(QUrl.FullyEncoded)
        return f"{source_url}?v={int(os.path.getmtime(absolute_path))}"


class _IdleCandidateLibrary:
    def __init__(self, current_character_id: str | None, idle_candidates: list[dict[str, object]]):
        self._current_character_id = current_character_id
        self._idle_candidates = list(idle_candidates)

    def get_current_character_id(self):
        return self._current_character_id

    def get_idle_motion_candidates(self, _character_id: str | None):
        return list(self._idle_candidates)


class _ScopedIdleCandidateLibrary:
    def __init__(self, current_character_id: str | None, idle_candidates_by_character: dict[str, list[dict[str, object]]]):
        self._current_character_id = current_character_id
        self._idle_candidates_by_character = {
            character_id: list(candidates)
            for character_id, candidates in idle_candidates_by_character.items()
        }

    def get_current_character_id(self):
        return self._current_character_id

    def get_idle_motion_candidates(self, character_id: str | None):
        return list(self._idle_candidates_by_character.get(character_id or "", []))


class _IdleRestoreHarness(_ChangeVideoHarness):
    DEMO_MOTION_MAPPING = {"idle": "Idle.webm"}

    def __init__(self, library, demo_dir: str):
        super().__init__()
        self._library = library
        self.DEMO_ANIMATIONS_DIR = demo_dir

    def _set_idle_motion_candidates(self, character_id: str | None) -> list[dict[str, object]]:
        if not character_id:
            self._run_javascript("setIdleMotionCandidates", [])
            return []

        raw_candidates = self._library.get_idle_motion_candidates(character_id)
        payload: list[dict[str, object]] = []
        for candidate in raw_candidates:
            absolute_path = self._resolve_media_path(candidate.get("path"))
            if not absolute_path or not os.path.isfile(absolute_path):
                continue
            payload.append(
                {
                    "source": self._build_media_source_url(absolute_path),
                    "weight": max(1, int(candidate.get("weight") or 1)),
                }
            )

        self._run_javascript("setIdleMotionCandidates", payload)
        return payload

    def restore_idle_video(self) -> bool:
        current_character_id = self._library.get_current_character_id()
        if current_character_id:
            idle_candidates = self._set_idle_motion_candidates(current_character_id)
            if idle_candidates:
                self._run_javascript("restoreIdleMotion")
                return True

        self._run_javascript("setIdleMotionCandidates", [])
        demo_idle_path = os.path.join(self.DEMO_ANIMATIONS_DIR, self.DEMO_MOTION_MAPPING["idle"])
        if os.path.isfile(demo_idle_path):
            return self.change_video(demo_idle_path, loop=True)
        return False


class _ManualQueuedTTSWorker:
    instances: list["_ManualQueuedTTSWorker"] = []

    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        parent=None,
    ):
        del parent
        self.text = text
        self.reply_id = reply_id or "manual-reply"
        self.trace_id = trace_id or ""
        self.finished_signal = _DebugSignal()
        self.progress_signal = _DebugSignal()
        self.audio_ready_signal = _DebugSignal()
        self.finished = _DebugSignal()
        self.started = False
        _ManualQueuedTTSWorker.instances.append(self)

    def start(self):
        self.started = True
        self.progress_signal.emit(
            "stream_started",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "bytes_forwarded": len(self.text.encode("utf-8")),
            },
        )

    def complete(self):
        self.progress_signal.emit(
            "driver_started",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
            },
        )
        payload = {
            "reply_id": self.reply_id,
            "trace_id": self.trace_id,
            "text": self.text,
        }
        self.finished_signal.emit(True, "語音生成完成。", payload)
        self.finished.emit()

    def deleteLater(self):
        return None


class _GuardedQueuedTTSWorker:
    instances: list["_GuardedQueuedTTSWorker"] = []

    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        playback_guard=None,
        parent=None,
    ):
        del parent, voice_id
        self.text = text
        self.reply_id = reply_id or "guarded-reply"
        self.trace_id = trace_id or ""
        self.playback_guard = playback_guard
        self.finished_signal = _DebugSignal()
        self.progress_signal = _DebugSignal()
        self.audio_ready_signal = _DebugSignal()
        self.finished = _DebugSignal()
        self.started = False
        _GuardedQueuedTTSWorker.instances.append(self)

    def start(self):
        self.started = True
        self.progress_signal.emit(
            "stream_started",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "bytes_forwarded": len(self.text.encode("utf-8")),
            },
        )

    def complete(self):
        payload = {
            "reply_id": self.reply_id,
            "trace_id": self.trace_id,
            "text": self.text,
        }
        if callable(self.playback_guard) and self.playback_guard(self.trace_id, self.reply_id) is False:
            payload["suppressed"] = True
            self.finished_signal.emit(False, "因 timeout_promoted 抑制晚到音訊。", payload)
            self.finished.emit()
            return
        self.progress_signal.emit(
            "driver_started",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
            },
        )
        self.finished_signal.emit(True, "語音生成完成。", payload)
        self.finished.emit()

    def deleteLater(self):
        return None


class _DebugSignal:
    def __init__(self):
        self._callbacks: list[object] = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _ImmediateTTSWorker:
    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        parent=None,
    ):
        del parent
        self._text = text
        self._reply_id = reply_id or "debug-reply"
        self._trace_id = trace_id or ""
        self.finished_signal = _DebugSignal()
        self.progress_signal = _DebugSignal()
        self.audio_ready_signal = _DebugSignal()
        self.finished = _DebugSignal()

    def start(self):
        self.progress_signal.emit(
            "stream_started",
            {
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
                "bytes_forwarded": len(self._text.encode("utf-8")),
            },
        )
        self.progress_signal.emit(
            "driver_started",
            {
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
            },
        )
        payload = {
            "reply_id": self._reply_id,
            "trace_id": self._trace_id,
            "text": self._text,
        }
        self.finished_signal.emit(True, "語音生成完成。", payload)
        self.finished.emit()

    def deleteLater(self):
        return None


class _ImmediateServiceWorker:
    def __init__(self, success: bool = True, message: str = "", payload: object | None = None, parent=None):
        del parent
        self._success = success
        self._message = message
        self._payload = payload
        self.finished_signal = _DebugSignal()
        self.finished = _DebugSignal()

    def start(self):
        self.finished_signal.emit(self._success, self._message, self._payload)
        self.finished.emit()

    def deleteLater(self):
        return None


class _FakePcmSessionPlayer:
    def __init__(self):
        self.chunks: list[bytes] = []

    def play_chunks(self, chunks, before_start=None):
        started = False
        total = 0
        for chunk in chunks:
            if not chunk:
                continue
            if not started:
                started = True
                if callable(before_start):
                    before_start()
            payload = bytes(chunk)
            self.chunks.append(payload)
            total += len(payload)
        return total


class _ManualPcmSessionWorker:
    instances: list["_ManualPcmSessionWorker"] = []

    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        pcm_stream_sink=None,
        parent=None,
    ):
        del parent, voice_id
        self.text = text
        self.reply_id = reply_id or "pcm-reply"
        self.trace_id = trace_id or ""
        self.pcm_stream_sink = pcm_stream_sink
        self.finished_signal = _DebugSignal()
        self.progress_signal = _DebugSignal()
        self.audio_ready_signal = _DebugSignal()
        self.finished = _DebugSignal()
        self.started = False
        _ManualPcmSessionWorker.instances.append(self)

    def start(self):
        self.started = True
        payload = self.text.encode("utf-8")
        self.progress_signal.emit(
            "stream_started",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "bytes_forwarded": len(payload),
                "format": "pcm",
                "transport": "http",
            },
        )
        midpoint = max(1, len(payload) // 2)
        first = payload[:midpoint]
        second = payload[midpoint:]
        if self.pcm_stream_sink is not None:
            self.pcm_stream_sink.enqueue_pcm_chunk(first, self.reply_id, self.trace_id)
            if second:
                self.pcm_stream_sink.enqueue_pcm_chunk(second, self.reply_id, self.trace_id)
            self.pcm_stream_sink.finish_pcm_segment(self.reply_id, self.trace_id)
        result_payload = {
            "reply_id": self.reply_id,
            "trace_id": self.trace_id,
            "text": self.text,
            "format": "pcm",
            "transport": "http",
            "queued_playback": True,
            "pcm_stream_session": True,
        }
        self.finished_signal.emit(True, "PCM handoff 完成。", result_payload)
        self.finished.emit()

    def deleteLater(self):
        return None


class _ManualServiceWorker:
    instances: list["_ManualServiceWorker"] = []

    def __init__(self, success: bool = True, message: str = "", payload: object | None = None, parent=None):
        del parent
        self._success = success
        self._message = message
        self._payload = payload
        self.finished_signal = _DebugSignal()
        self.finished = _DebugSignal()
        self.started = False
        _ManualServiceWorker.instances.append(self)

    def start(self):
        self.started = True

    def complete(self):
        self.finished_signal.emit(self._success, self._message, self._payload)
        self.finished.emit()

    def deleteLater(self):
        return None


class _StickyFallbackTTSWorker:
    preferred_providers: list[str] = []
    started_count = 0

    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        fallback_voice_id: str | None = None,
        preferred_provider: str | None = None,
        playback_guard=None,
        parent=None,
    ):
        del text, voice_id, fallback_voice_id, playback_guard, parent
        self.reply_id = reply_id or "sticky-reply"
        self.trace_id = trace_id or ""
        self.preferred_provider = preferred_provider or ""
        self.finished_signal = _DebugSignal()
        self.progress_signal = _DebugSignal()
        self.audio_ready_signal = _DebugSignal()
        self.finished = _DebugSignal()
        _StickyFallbackTTSWorker.preferred_providers.append(self.preferred_provider)

    def start(self):
        _StickyFallbackTTSWorker.started_count += 1
        if _StickyFallbackTTSWorker.started_count == 1 and not self.preferred_provider:
            self.progress_signal.emit(
                "provider_selected",
                {
                    "reply_id": self.reply_id,
                    "trace_id": self.trace_id,
                    "provider": "voai",
                    "reason": "initial",
                },
            )
            self.progress_signal.emit(
                "fallback_triggered",
                {
                    "reply_id": self.reply_id,
                    "trace_id": self.trace_id,
                    "from_provider": "voai",
                    "to_provider": "elevenlabs",
                    "failure_code": "http_529",
                },
            )
            self.progress_signal.emit(
                "provider_selected",
                {
                    "reply_id": self.reply_id,
                    "trace_id": self.trace_id,
                    "provider": "elevenlabs",
                    "reason": "fast_fail",
                    "fallback_locked": True,
                },
            )
        else:
            self.progress_signal.emit(
                "provider_selected",
                {
                    "reply_id": self.reply_id,
                    "trace_id": self.trace_id,
                    "provider": self.preferred_provider or "elevenlabs",
                    "reason": "trace_locked",
                    "fallback_locked": True,
                },
            )
        self.progress_signal.emit(
            "driver_started",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
            },
        )
        self.finished_signal.emit(
            True,
            "fallback ok",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "provider": "elevenlabs",
                "selected_provider": "elevenlabs",
            },
        )
        self.finished.emit()

    def deleteLater(self):
        return None


class _CriticalFailureTTSWorker:
    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        fallback_voice_id: str | None = None,
        preferred_provider: str | None = None,
        playback_guard=None,
        parent=None,
    ):
        del text, reply_id, voice_id, fallback_voice_id, preferred_provider, playback_guard, parent
        self.trace_id = trace_id or ""
        self.finished_signal = _DebugSignal()
        self.progress_signal = _DebugSignal()
        self.audio_ready_signal = _DebugSignal()
        self.finished = _DebugSignal()

    def start(self):
        self.progress_signal.emit(
            "provider_selected",
            {
                "reply_id": "critical-reply",
                "trace_id": self.trace_id,
                "provider": "voai",
                "reason": "initial",
            },
        )
        self.progress_signal.emit(
            "fallback_triggered",
            {
                "reply_id": "critical-reply",
                "trace_id": self.trace_id,
                "from_provider": "voai",
                "to_provider": "elevenlabs",
                "failure_code": "http_529",
            },
        )
        self.progress_signal.emit(
            "provider_selected",
            {
                "reply_id": "critical-reply",
                "trace_id": self.trace_id,
                "provider": "elevenlabs",
                "reason": "fast_fail",
                "fallback_locked": True,
            },
        )
        self.progress_signal.emit(
            "critical_tts_failure",
            {
                "reply_id": "critical-reply",
                "trace_id": self.trace_id,
                "provider_chain": ["voai", "elevenlabs"],
            },
        )
        self.finished_signal.emit(
            False,
            "雙 provider TTS 都失敗，已改為文字回覆。",
            {
                "reply_id": "critical-reply",
                "trace_id": self.trace_id,
                "provider": "elevenlabs",
                "selected_provider": "elevenlabs",
                "critical_tts_failure": True,
                "text_only": True,
                "provider_chain": ["voai", "elevenlabs"],
            },
        )
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
        dispatcher.shutdown()

        return {
            "dispatched": dispatched,
            "status_calls": window.status_calls,
            "motion_calls": window.motion_calls,
            "motion_asset_calls": window.motion_asset_calls,
            "audio_calls": window.audio_calls,
            "call_order": window.call_order,
            "ok": (
                dispatched
                and not window.audio_calls
                and window.motion_calls == ["listen"]
                and bool(window.motion_asset_calls)
                and window.motion_asset_calls[0][1].endswith("listen.webm")
                and len(window.call_order) >= 2
                and window.call_order[0] == ("status", "這是一段測試語音。")
                and window.call_order[1] == ("motion", "listen")
            ),
        }


def run_streamed_action_first_debug_probe() -> dict[str, object]:
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

        dispatched_action = dispatcher.dispatch("[ACTION:listen]")
        dispatched_chunk = dispatcher.dispatch("第一句測試語音。")
        dispatcher.shutdown()

        return {
            "dispatched_action": dispatched_action,
            "dispatched_chunk": dispatched_chunk,
            "status_calls": window.status_calls,
            "motion_calls": window.motion_calls,
            "motion_asset_calls": window.motion_asset_calls,
            "audio_calls": window.audio_calls,
            "call_order": window.call_order,
            "ok": (
                dispatched_action
                and dispatched_chunk
                and window.motion_calls == ["listen"]
                and not window.audio_calls
                and len(window.call_order) >= 3
                and window.call_order[0] == ("status", "正在專心聆聽")
                and window.call_order[1] == ("motion", "listen")
                and window.call_order[2] == ("status", "第一句測試語音。")
            ),
        }


class ActionPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        _ManualQueuedTTSWorker.instances.clear()
        _GuardedQueuedTTSWorker.instances.clear()
        _ManualServiceWorker.instances.clear()
        _StickyFallbackTTSWorker.preferred_providers.clear()
        _StickyFallbackTTSWorker.started_count = 0

    def test_missing_motion_falls_back_to_idle_with_warning(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-fallback-") as temp_dir:
            idle_path = Path(temp_dir) / "Idle.webm"
            idle_path.write_bytes(b"idle")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(window, library=_NoopLibrary(), tts_enabled=False)
            stdout_buffer = io.StringIO()
            with redirect_stdout(stdout_buffer):
                dispatched = dispatcher.dispatch("[ACTION:wave_response]")

            self.assertTrue(dispatched)
            self.assertEqual(len(window.played_assets), 1)
            motion_key, played_path, loop = window.played_assets[0]
            self.assertEqual(motion_key, "wave_response")
            self.assertEqual(played_path, os.path.abspath(str(idle_path)))
            self.assertTrue(loop)
            self.assertEqual(window.restore_idle_calls, 0)
            self.assertIn("[ECHOES WARNING] 找不到動作檔案: wave_response, 退回 Idle", stdout_buffer.getvalue())

            dispatcher.shutdown()

    def test_tts_dispatch_keeps_motion_before_audio(self):
        result = run_tts_dispatch_debug_probe()
        self.assertTrue(result["ok"], result)

    def test_streamed_reply_parser_normalizes_alias_action_to_supported_action(self):
        parser = StreamedReplyParser()
        outputs = parser.feed("[ACTION:news]我來幫你看今天重點。")
        outputs.extend(parser.flush())
        self.assertEqual(outputs, ["[ACTION:report_news]", "我來幫你看今天重點。"])

    def test_streamed_reply_parser_emits_action_then_sentence_chunks(self):
        parser = StreamedReplyParser()

        outputs = []
        outputs.extend(parser.feed("[ACTION:listen]哈囉，"))
        outputs.extend(parser.feed("今天一起加油。"))
        outputs.extend(parser.flush())

        self.assertEqual(outputs, ["[ACTION:listen]", "哈囉，今天一起加油。"])

    def test_streamed_reply_parser_flushes_trailing_text_without_punctuation(self):
        parser = StreamedReplyParser()

        outputs = []
        outputs.extend(parser.feed("這是一段"))
        outputs.extend(parser.feed("還沒結尾"))
        outputs.extend(parser.flush())

        self.assertEqual(outputs, ["這是一段還沒結尾"])

    def test_streamed_reply_parser_keeps_commas_and_normalizes_newlines_within_sentence(self):
        parser = StreamedReplyParser()

        outputs = []
        outputs.extend(parser.feed("[ACTION:listen]Hi,"))
        outputs.extend(parser.feed(" next line\n"))
        outputs.extend(parser.feed("still going."))
        outputs.extend(parser.flush())

        self.assertEqual(outputs, ["[ACTION:listen]", "Hi, next line", "still going."])

    def test_dispatcher_accepts_alias_action_name(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-alias-") as temp_dir:
            report_news_path = Path(temp_dir) / "report_news.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            report_news_path.write_bytes(b"news")
            idle_path.write_bytes(b"idle")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                news_worker_factory=lambda parent=None: _ImmediateServiceWorker(
                    success=True,
                    message="新聞已完成",
                    payload={"headline": "測試頭條"},
                    parent=parent,
                ),
                tts_enabled=False,
            )
            stdout_buffer = io.StringIO()
            with redirect_stdout(stdout_buffer):
                dispatched = dispatcher.dispatch("[ACTION:news] 今天幫你整理頭條")

            self.assertTrue(dispatched)
            self.assertEqual(window.played_assets[0][0], "report_news")
            self.assertEqual(window.played_assets[0][1], os.path.abspath(str(report_news_path)))
            self.assertIn("action alias `news` 已正規化為 `report_news`", stdout_buffer.getvalue())

            dispatcher.shutdown()

    def test_change_video_uses_encoded_file_url_and_direct_js_for_temporary_motion(self):
        with tempfile.TemporaryDirectory(prefix="echoes-change-video-") as temp_dir:
            webm_path = Path(temp_dir) / "初音 demo's motion.webm"
            webm_path.write_bytes(b"webm")

            harness = _ChangeVideoHarness()
            changed = harness.change_video(str(webm_path), loop=False)

            self.assertTrue(changed)
            expected_url = QUrl.fromLocalFile(os.path.abspath(str(webm_path))).toString(QUrl.FullyEncoded)
            expected_script = (
                "if (window.playTemporaryVideo) { "
                "window.playTemporaryVideo('"
                f"{_ChangeVideoHarness._escape_javascript_single_quoted_string(expected_url)}"
                "');"
                " } else { console.error('[ECHOES] playTemporaryVideo bridge 不存在'); }"
            )
            self.assertEqual(harness.web_view.page().scripts, [expected_script])

    def test_restore_idle_video_pushes_idle_candidates_before_js_restore(self):
        with tempfile.TemporaryDirectory(prefix="echoes-idle-bridge-") as temp_dir:
            idle_path = Path(temp_dir) / "Idle.webm"
            guitar_path = Path(temp_dir) / "Idle_Guitar.webm"
            idle_path.write_bytes(b"idle")
            guitar_path.write_bytes(b"guitar")

            library = _IdleCandidateLibrary(
                "miku",
                [
                    {"path": str(idle_path), "weight": 4},
                    {"path": str(guitar_path), "weight": 2},
                ],
            )
            harness = _IdleRestoreHarness(library, temp_dir)

            restored = harness.restore_idle_video()

            self.assertTrue(restored)
            self.assertEqual(len(harness.web_view.page().scripts), 2)
            self.assertIn('"setIdleMotionCandidates"', harness.web_view.page().scripts[0])
            self.assertIn("Idle.webm", harness.web_view.page().scripts[0])
            self.assertIn("Idle_Guitar.webm", harness.web_view.page().scripts[0])
            self.assertIn('"restoreIdleMotion"', harness.web_view.page().scripts[1])

    def test_restore_idle_video_only_pushes_current_character_candidates(self):
        with tempfile.TemporaryDirectory(prefix="echoes-idle-scope-") as temp_dir:
            miku_dir = Path(temp_dir) / "miku"
            choppr_dir = Path(temp_dir) / "Choppr"
            miku_dir.mkdir()
            choppr_dir.mkdir()

            miku_idle = miku_dir / "Idle.webm"
            choppr_idle = choppr_dir / "Idle.webm"
            choppr_reading = choppr_dir / "Idle_reading.webm"
            miku_idle.write_bytes(b"miku")
            choppr_idle.write_bytes(b"choppr")
            choppr_reading.write_bytes(b"reading")

            library = _ScopedIdleCandidateLibrary(
                "Choppr",
                {
                    "miku": [{"path": str(miku_idle), "weight": 4}],
                    "Choppr": [
                        {"path": str(choppr_idle), "weight": 4},
                        {"path": str(choppr_reading), "weight": 2},
                    ],
                },
            )
            harness = _IdleRestoreHarness(library, temp_dir)

            restored = harness.restore_idle_video()

            self.assertTrue(restored)
            candidate_script = harness.web_view.page().scripts[0]
            self.assertIn("Choppr/Idle.webm", candidate_script)
            self.assertIn("Choppr/Idle_reading.webm", candidate_script)
            self.assertNotIn("miku/Idle.webm", candidate_script)

    def test_streamed_action_prefix_starts_motion_before_text_chunk(self):
        result = run_streamed_action_first_debug_probe()
        self.assertTrue(result["ok"], result)

    def test_dispatcher_completes_correlated_trace_after_immediate_tts(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-trace-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            tracker = InteractionLatencyTracker()
            trace_id = tracker.begin_interaction("test", "你好")
            tracker.mark_brain_queued(trace_id)
            tracker.mark_brain_started(trace_id)
            tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ImmediateTTSWorker,
                latency_tracker=tracker,
            )

            dispatched = dispatcher.dispatch("[ACTION:listen] 你好。", trace_id=trace_id)
            tracker.mark_brain_completed(trace_id)

            self.assertTrue(dispatched)
            self.assertEqual(window.played_assets[0][0], "listen")
            self.assertIsNone(tracker.snapshot(trace_id))

            dispatcher.shutdown()

    def test_dispatch_can_skip_immediate_tts_for_streamed_fragments(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-no-tts-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualQueuedTTSWorker,
            )

            self.assertTrue(
                dispatcher.dispatch("[ACTION:listen] 第一段測試語音。", allow_tts=False)
            )
            self.assertEqual(window.played_assets[0][0], "listen")
            self.assertEqual(len(_ManualQueuedTTSWorker.instances), 0)

            dispatcher.shutdown()

    def test_dispatcher_serializes_tts_queue_without_overlap(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-queue-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualQueuedTTSWorker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:listen] 第一段。"))
            self.assertTrue(dispatcher.dispatch("第二段。"))
            self.assertEqual(len(_ManualQueuedTTSWorker.instances), 1)
            self.assertTrue(_ManualQueuedTTSWorker.instances[0].started)

            _ManualQueuedTTSWorker.instances[0].complete()

            self.assertEqual(len(_ManualQueuedTTSWorker.instances), 2)
            self.assertTrue(_ManualQueuedTTSWorker.instances[1].started)

            dispatcher.shutdown()

    def test_timeout_promoted_action_suppresses_late_audio_and_duplicate_motion(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-timeout-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            tracker = InteractionLatencyTracker()
            trace_id = tracker.begin_interaction("test", "晚到語音")
            tracker.mark_brain_started(trace_id)
            tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_GuardedQueuedTTSWorker,
                latency_tracker=tracker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:listen] 晚一點才開口。", trace_id=trace_id))
            tracker.mark_brain_completed(trace_id)
            dispatcher._promote_pending_action(trace_id)

            self.assertEqual(len(window.played_assets), 1)
            self.assertEqual(window.played_assets[0][0], "listen")
            snapshot = tracker.snapshot(trace_id)
            self.assertIsNotNone(snapshot)
            self.assertTrue(snapshot["timeout_promoted"])
            self.assertEqual(len(_GuardedQueuedTTSWorker.instances), 1)

            _GuardedQueuedTTSWorker.instances[0].complete()

            completed = tracker.get_completed_trace(trace_id)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertTrue(completed["timeout_promoted"])
            self.assertEqual(completed["tts_failures"], 1)
            self.assertEqual(len(window.played_assets), 1)
            self.assertNotIn("first_driver_started", " ".join(completed["milestones"]))

            dispatcher.shutdown()

    def test_play_music_fast_path_marks_trace_as_tts_skipped_by_design(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-play-music-trace-fast-") as temp_dir:
            play_music_path = Path(temp_dir) / "play_music.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            panel_path = Path(temp_dir) / "Play_Music_Panel.webm"
            play_music_path.write_bytes(b"music")
            idle_path.write_bytes(b"idle")
            panel_path.write_bytes(b"panel")

            tracker = InteractionLatencyTracker()
            trace_id = tracker.begin_interaction("test", "我想聽音樂")
            tracker.mark_brain_queued(trace_id)
            tracker.mark_brain_started(trace_id)
            tracker.mark_fragment_emitted(trace_id, "[ACTION:play_music]")
            tracker.mark_tts_expected(trace_id, "我想聽音樂。")

            window = _LoopActionProbeWindow(temp_dir)
            library = _PanelLibrary({"play_music": str(panel_path)})
            dispatcher = ActionDispatcher(
                window,
                library=library,
                music_worker_factory=lambda parent=None: _ManualServiceWorker(
                    success=True,
                    message="音樂已完成",
                    payload={"path": "song.mp3", "title": "Test Song"},
                    parent=parent,
                ),
                motion_path_resolver=lambda motion_key: str(
                    {"play_music": play_music_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualQueuedTTSWorker,
                latency_tracker=tracker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:play_music] 我想聽音樂。", trace_id=trace_id))

            snapshot = tracker.snapshot(trace_id)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot["tts_finished"], 1)
            self.assertEqual(snapshot["tts_failures"], 0)
            self.assertTrue(snapshot["tts_skipped_by_design"])
            self.assertFalse(snapshot["timeout_promoted"])

            tracker.mark_brain_completed(trace_id)

            completed = tracker.get_completed_trace(trace_id)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(completed["tts_failures"], 0)
            self.assertTrue(completed["tts_skipped_by_design"])
            self.assertFalse(completed["timeout_promoted"])

            dispatcher.shutdown()

    def test_dispatcher_locks_fallback_provider_for_later_chunks_in_same_trace(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-provider-lock-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            tracker = InteractionLatencyTracker()
            trace_id = tracker.begin_interaction("test", "provider lock")
            tracker.mark_brain_started(trace_id)
            tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_StickyFallbackTTSWorker,
                latency_tracker=tracker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:listen] 第一段。", trace_id=trace_id))
            self.assertTrue(dispatcher.dispatch("第二段。", trace_id=trace_id))
            tracker.mark_brain_completed(trace_id)

            self.assertEqual(_StickyFallbackTTSWorker.preferred_providers[0], "")
            self.assertEqual(_StickyFallbackTTSWorker.preferred_providers[1], "elevenlabs")
            completed = tracker.get_completed_trace(trace_id)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertTrue(completed["fallback_triggered"])
            self.assertEqual(completed["selected_tts_provider"], "elevenlabs")

            dispatcher.shutdown()

    def test_dispatcher_reuses_single_driver_started_for_pcm_session_trace(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-pcm-session-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            tracker = InteractionLatencyTracker()
            trace_id = tracker.begin_interaction("test", "連續 PCM")
            tracker.mark_brain_started(trace_id)
            tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualPcmSessionWorker,
                latency_tracker=tracker,
            )
            fake_pcm_player = _FakePcmSessionPlayer()
            dispatcher._audio_worker._pcm_player_factory = lambda sample_rate, channels: fake_pcm_player

            self.assertTrue(dispatcher.dispatch("[ACTION:listen] 第一段比較長的測試語音。", trace_id=trace_id))
            self.assertTrue(dispatcher.dispatch("第二段也接續進來，不要重開 driver。", trace_id=trace_id))
            tracker.mark_brain_completed(trace_id)
            dispatcher.complete_tts_trace(trace_id)

            deadline = time.time() + 2.0
            completed = None
            while time.time() < deadline:
                QCoreApplication.processEvents()
                completed = tracker.get_completed_trace(trace_id)
                if completed is not None and not dispatcher._audio_worker.is_busy():
                    break
                time.sleep(0.02)

            self.assertEqual(len(dispatcher._driver_started_replies), 1)
            self.assertEqual(len(window.played_assets), 1)
            self.assertGreaterEqual(len(fake_pcm_player.chunks), 2)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(completed["tts_failures"], 0)
            self.assertIn("first_driver_started", " ".join(completed["milestones"]))
            self.assertFalse(dispatcher._audio_worker.is_busy())

            dispatcher.shutdown()

    def test_duplicate_report_news_dispatch_does_not_restart_pending_action(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-report-news-reentry-") as temp_dir:
            report_news_path = Path(temp_dir) / "report_news.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            panel_path = Path(temp_dir) / "News_Panel.webm"
            report_news_path.write_bytes(b"news")
            idle_path.write_bytes(b"idle")
            panel_path.write_bytes(b"panel")

            window = _LoopActionProbeWindow(temp_dir)
            library = _PanelLibrary({"report_news": str(panel_path)})
            dispatcher = ActionDispatcher(
                window,
                library=library,
                news_worker_factory=lambda parent=None: _ManualServiceWorker(
                    success=True,
                    message="新聞已完成",
                    payload={"headline": "測試頭條"},
                    parent=parent,
                ),
                motion_path_resolver=lambda motion_key: str(
                    {"report_news": report_news_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_enabled=False,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:report_news]", trace_id="trace-news"))
            self.assertTrue(dispatcher.dispatch("[ACTION:report_news]", trace_id="trace-news"))

            self.assertEqual(len(_ManualServiceWorker.instances), 1)
            self.assertEqual(len(window.panel_calls), 1)
            self.assertTrue(window.panel_calls[0][1])
            self.assertTrue(window.panel_calls[0][2])
            self.assertIn("trace-news", dispatcher._suppressed_traces)

            dispatcher.shutdown()

    def test_duplicate_play_music_dispatch_does_not_restart_active_loop_action(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-play-music-reentry-") as temp_dir:
            play_music_path = Path(temp_dir) / "play_music.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            panel_path = Path(temp_dir) / "Play_Music_Panel.webm"
            play_music_path.write_bytes(b"music")
            idle_path.write_bytes(b"idle")
            panel_path.write_bytes(b"panel")

            window = _LoopActionProbeWindow(temp_dir)
            library = _PanelLibrary({"play_music": str(panel_path)})
            dispatcher = ActionDispatcher(
                window,
                library=library,
                music_worker_factory=lambda parent=None: _ManualServiceWorker(
                    success=True,
                    message="音樂已完成",
                    payload={"path": "song.mp3", "title": "Test Song"},
                    parent=parent,
                ),
                motion_path_resolver=lambda motion_key: str(
                    {"play_music": play_music_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_enabled=False,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:play_music]"))
            self.assertTrue(dispatcher.dispatch("[ACTION:play_music]"))

            self.assertEqual(len(_ManualServiceWorker.instances), 0)
            self.assertEqual(len(window.played_assets), 1)
            self.assertFalse(window.played_assets[0][2])
            self.assertEqual(len(window.panel_calls), 1)
            self.assertFalse(window.panel_calls[0][1])
            self.assertFalse(window.panel_calls[0][2])

            dispatcher.shutdown()

    def test_play_music_uses_panel_audio_once_without_tts_or_room_audio(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-play-music-panel-fallback-") as temp_dir:
            play_music_path = Path(temp_dir) / "play_music.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            panel_path = Path(temp_dir) / "Play_Music_Panel.webm"
            play_music_path.write_bytes(b"music")
            idle_path.write_bytes(b"idle")
            panel_path.write_bytes(b"panel")

            window = _LoopActionProbeWindow(temp_dir)
            window.play_music_result = False
            library = _PanelLibrary({"play_music": str(panel_path)})
            dispatcher = ActionDispatcher(
                window,
                library=library,
                music_worker_factory=lambda parent=None: _ImmediateServiceWorker(
                    success=True,
                    message="音樂已完成",
                    payload={"path": "song.mp3", "title": "Test Song"},
                    parent=parent,
                ),
                motion_path_resolver=lambda motion_key: str(
                    {"play_music": play_music_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualQueuedTTSWorker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:play_music] 我想聽音樂。"))

            self.assertEqual(len(_ManualQueuedTTSWorker.instances), 0)
            self.assertEqual(len(window.audio_calls), 0)
            self.assertEqual(len(window.played_assets), 1)
            self.assertFalse(window.played_assets[0][2])
            self.assertEqual(len(window.panel_calls), 1)
            self.assertFalse(window.panel_calls[0][1])
            self.assertFalse(window.panel_calls[0][2])
            self.assertEqual(window.panel_mute_updates, [])

            dispatcher.shutdown()

    def test_report_news_waits_for_room_audio_end_without_tts_output(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-report-news-audio-end-") as temp_dir:
            report_news_path = Path(temp_dir) / "report_news.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            panel_path = Path(temp_dir) / "News_Panel.webm"
            report_news_path.write_bytes(b"news")
            idle_path.write_bytes(b"idle")
            panel_path.write_bytes(b"panel")

            window = _LoopActionProbeWindow(temp_dir)
            library = _PanelLibrary({"report_news": str(panel_path)})
            dispatcher = ActionDispatcher(
                window,
                library=library,
                news_worker_factory=lambda parent=None: _ImmediateServiceWorker(
                    success=True,
                    message="新聞已完成",
                    payload={
                        "headline": "測試頭條",
                        "title": "固定新聞播報",
                        "path": "news.mp3",
                        "cached": True,
                    },
                    parent=parent,
                ),
                motion_path_resolver=lambda motion_key: str(
                    {"report_news": report_news_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualQueuedTTSWorker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:report_news] 我來播報新聞。"))

            self.assertEqual(len(_ManualQueuedTTSWorker.instances), 0)
            self.assertEqual(len(window.audio_calls), 1)
            self.assertEqual(len(window.motion_loop_calls), 1)
            self.assertEqual(len(window.panel_calls), 1)
            self.assertTrue(window.panel_calls[0][2])
            self.assertTrue(dispatcher._wait_for_room_audio_ended)
            self.assertEqual(dispatcher._current_loop_action_key, "report_news")

            dispatcher._on_panel_video_ended()
            dispatcher._on_main_video_ended()

            self.assertEqual(dispatcher._current_loop_action_key, "report_news")

            dispatcher._on_room_audio_ended()

            self.assertIsNone(dispatcher._current_loop_action_key)

            dispatcher.shutdown()

    def test_play_music_waits_for_panel_video_end_before_idle(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-play-music-no-tts-") as temp_dir:
            play_music_path = Path(temp_dir) / "play_music.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            panel_path = Path(temp_dir) / "Play_Music_Panel.webm"
            play_music_path.write_bytes(b"music")
            idle_path.write_bytes(b"idle")
            panel_path.write_bytes(b"panel")

            window = _LoopActionProbeWindow(temp_dir)
            library = _PanelLibrary({"play_music": str(panel_path)})
            dispatcher = ActionDispatcher(
                window,
                library=library,
                motion_path_resolver=lambda motion_key: str(
                    {"play_music": play_music_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_enabled=False,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:play_music]"))
            self.assertIsNotNone(dispatcher._loop_cleanup_timer)
            self.assertEqual(dispatcher._current_loop_action_key, "play_music")
            self.assertEqual(dispatcher._current_loop_action_key, "play_music")
            self.assertEqual(window.stop_motion_loop_calls, 0)
            self.assertEqual(window.restore_idle_calls, 0)

            dispatcher._on_main_video_ended()

            self.assertEqual(dispatcher._current_loop_action_key, "play_music")

            dispatcher._on_panel_video_ended()

            self.assertIsNone(dispatcher._current_loop_action_key)
            self.assertEqual(window.stop_motion_loop_calls, 1)
            self.assertGreaterEqual(window.restore_idle_calls, 1)

            dispatcher.shutdown()

    def test_next_action_waits_for_play_music_panel_video_end(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-play-music-queue-") as temp_dir:
            play_music_path = Path(temp_dir) / "play_music.webm"
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            panel_path = Path(temp_dir) / "Play_Music_Panel.webm"
            play_music_path.write_bytes(b"music")
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")
            panel_path.write_bytes(b"panel")

            window = _LoopActionProbeWindow(temp_dir)
            library = _PanelLibrary({"play_music": str(panel_path)})
            dispatcher = ActionDispatcher(
                window,
                library=library,
                motion_path_resolver=lambda motion_key: str(
                    {
                        "play_music": play_music_path,
                        "listen": listen_path,
                        "idle": idle_path,
                    }.get(motion_key, "")
                ),
                tts_enabled=False,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:play_music]"))
            self.assertTrue(dispatcher.dispatch("[ACTION:listen] 下一個任務"))

            self.assertEqual(dispatcher._current_loop_action_key, "play_music")
            self.assertEqual(len(window.played_assets), 1)
            self.assertFalse(window.played_assets[0][2])
            self.assertEqual(len(dispatcher._deferred_dispatches), 1)

            dispatcher._on_main_video_ended()

            self.assertEqual(dispatcher._current_loop_action_key, "play_music")

            dispatcher._on_panel_video_ended()

            self.assertEqual(dispatcher._current_loop_action_key, "listen")
            self.assertEqual(len(window.motion_loop_calls), 1)
            self.assertEqual(len(dispatcher._deferred_dispatches), 0)

            dispatcher.shutdown()

    def test_play_music_ignores_display_text_and_waits_for_panel_video_end(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-play-music-main-ended-") as temp_dir:
            play_music_path = Path(temp_dir) / "play_music.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            panel_path = Path(temp_dir) / "Play_Music_Panel.webm"
            play_music_path.write_bytes(b"music")
            idle_path.write_bytes(b"idle")
            panel_path.write_bytes(b"panel")

            window = _LoopActionProbeWindow(temp_dir)
            library = _PanelLibrary({"play_music": str(panel_path)})
            dispatcher = ActionDispatcher(
                window,
                library=library,
                motion_path_resolver=lambda motion_key: str(
                    {"play_music": play_music_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualQueuedTTSWorker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:play_music] 第一段語音。"))
            self.assertEqual(len(_ManualQueuedTTSWorker.instances), 0)

            self.assertEqual(dispatcher._current_loop_action_key, "play_music")
            self.assertEqual(window.stop_motion_loop_calls, 0)

            dispatcher._on_main_video_ended()

            self.assertEqual(dispatcher._current_loop_action_key, "play_music")

            dispatcher._on_panel_video_ended()

            self.assertIsNone(dispatcher._current_loop_action_key)
            self.assertEqual(window.stop_motion_loop_calls, 1)
            self.assertGreaterEqual(window.restore_idle_calls, 1)

            dispatcher.shutdown()

    def test_play_music_fast_path_starts_without_tts_worker(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-play-music-fast-path-") as temp_dir:
            play_music_path = Path(temp_dir) / "play_music.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            panel_path = Path(temp_dir) / "Play_Music_Panel.webm"
            play_music_path.write_bytes(b"music")
            idle_path.write_bytes(b"idle")
            panel_path.write_bytes(b"panel")

            window = _LoopActionProbeWindow(temp_dir)
            library = _PanelLibrary({"play_music": str(panel_path)})
            dispatcher = ActionDispatcher(
                window,
                library=library,
                music_worker_factory=lambda parent=None: _ManualServiceWorker(
                    success=True,
                    message="音樂已完成",
                    payload={"path": "song.mp3", "title": "Test Song"},
                    parent=parent,
                ),
                motion_path_resolver=lambda motion_key: str(
                    {"play_music": play_music_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualQueuedTTSWorker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:play_music] 我想聽音樂。", trace_id="trace-music-fast"))

            self.assertEqual(len(_ManualQueuedTTSWorker.instances), 0)
            self.assertEqual(len(_ManualServiceWorker.instances), 0)
            self.assertEqual(dispatcher._current_loop_action_key, "play_music")
            self.assertEqual(dispatcher._active_action_trace_id, "trace-music-fast")
            self.assertIn("trace-music-fast", dispatcher._suppressed_traces)
            self.assertEqual(len(window.motion_loop_calls), 0)
            self.assertEqual(len(window.played_assets), 1)
            self.assertFalse(window.played_assets[0][2])
            self.assertEqual(len(window.panel_calls), 1)
            self.assertFalse(window.panel_calls[0][1])
            self.assertFalse(window.panel_calls[0][2])

            dispatcher.shutdown()

    def test_wave_response_uses_cached_audio_and_waits_for_room_audio_and_main_video_end(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-wave-audio-") as temp_dir:
            wave_path = Path(temp_dir) / "Greeting.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            wave_path.write_bytes(b"wave")
            idle_path.write_bytes(b"idle")

            window = _LoopActionProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                wave_worker_factory=lambda parent=None: _ImmediateServiceWorker(
                    success=True,
                    message="揮手問候音檔已完成",
                    payload={"path": "wave.mp3", "title": "嗨 你好嗎"},
                    parent=parent,
                ),
                motion_path_resolver=lambda motion_key: str(
                    {"wave_response": wave_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualQueuedTTSWorker,
            )
            dispatcher._wave_greeting_audio_delay_ms = 80

            self.assertTrue(dispatcher.dispatch("[ACTION:wave_response] 嗨 你好嗎"))

            self.assertEqual(len(_ManualQueuedTTSWorker.instances), 0)
            self.assertEqual(len(window.motion_loop_calls), 1)
            self.assertEqual(len(window.audio_calls), 0)

            time.sleep(0.12)
            QCoreApplication.processEvents()

            self.assertEqual(len(window.audio_calls), 1)
            self.assertTrue(dispatcher._wait_for_room_audio_ended)
            self.assertEqual(dispatcher._current_loop_action_key, "wave_response")

            dispatcher._on_main_video_ended()

            self.assertEqual(dispatcher._current_loop_action_key, "wave_response")

            dispatcher._on_room_audio_ended()

            self.assertEqual(dispatcher._current_loop_action_key, "wave_response")

            dispatcher._on_main_video_ended()

            self.assertIsNone(dispatcher._current_loop_action_key)

            dispatcher.shutdown()

    def test_duplicate_driver_started_event_does_not_reactivate_pending_action(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-driver-dedupe-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_enabled=False,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:listen]", trace_id="trace-driver-dedupe"))
            dispatcher._on_driver_started("reply-1", "trace-driver-dedupe")
            dispatcher._on_driver_started("reply-1", "trace-driver-dedupe")

            self.assertEqual(len(dispatcher._driver_started_pairs), 1)
            self.assertEqual(len(window.played_assets), 1)

            dispatcher.shutdown()

    def test_non_news_music_action_still_finishes_immediately_on_tts_idle(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-listen-tts-idle-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            window = _LoopActionProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ManualQueuedTTSWorker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:listen] 第一段語音。"))
            self.assertEqual(len(_ManualQueuedTTSWorker.instances), 1)
            _ManualQueuedTTSWorker.instances[0].complete()

            self.assertIsNone(dispatcher._current_loop_action_key)
            self.assertEqual(window.stop_motion_loop_calls, 1)
            self.assertGreaterEqual(window.restore_idle_calls, 1)

            dispatcher.shutdown()

    def test_critical_tts_failure_cleans_pending_action_and_keeps_text_only_trace(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-critical-fail-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            tracker = InteractionLatencyTracker()
            trace_id = tracker.begin_interaction("test", "critical fail")
            tracker.mark_brain_started(trace_id)
            tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_CriticalFailureTTSWorker,
                latency_tracker=tracker,
            )

            self.assertTrue(dispatcher.dispatch("[ACTION:listen] 只保留文字。", trace_id=trace_id))
            tracker.mark_brain_completed(trace_id)

            self.assertNotIn(trace_id, dispatcher._pending_actions)
            completed = tracker.get_completed_trace(trace_id)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertTrue(completed["text_only_completed"])
            self.assertEqual(completed["selected_tts_provider"], "elevenlabs")
            self.assertEqual(completed["tts_failures"], 1)
            self.assertGreaterEqual(window.restore_idle_calls, 1)

            dispatcher.shutdown()


if __name__ == "__main__":
    unittest.main()
