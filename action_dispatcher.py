"""
ECHOES — Centralized action binding dispatcher
解析 action 指令並協調角色動作、背景服務與 UI 狀態更新。
"""

from __future__ import annotations

import os
import queue
import re
import inspect
from collections import deque
from uuid import uuid4
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt5.QtCore import QCoreApplication, QObject, QTimer

from action_services import (
    FixedIntentReplyWorker,
    MusicSelectionWorker,
    NewsFetchWorker,
    WaveGreetingWorker,
    resolve_fixed_intent_source_label,
)
from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker
from text_utils import sanitize_tts_text
from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker  # noqa: F401 — 保留供降級使用
from audio_worker import AudioStreamWorker
from character_library import ASSETS_WEBM_DIR
import config
from interaction_trace import InteractionLatencyTracker

if TYPE_CHECKING:
    from character_library import CharacterLibrary
    from ui.transparent_window import TransparentWindow

ACTION_DIRECTIVE_PATTERN = re.compile(
    r"(?:\[\s*ACTION\s*:\s*(?P<bracket>[A-Za-z0-9_-]+)\s*\]|(?<!\w)ACTION\s*:\s*(?P<bare>[A-Za-z0-9_-]+))",
    re.IGNORECASE,
)
REPLY_STATUS_LABEL = "正在回覆"
#為了兼顧 VOAI 和 ElevenLabs 的 TTS 響應時間，並給予角色動作足夠的播放時間，設定新聞播報的語音觸發延遲為 2.5 秒。這樣可以確保在大多數情況下，角色的新聞播報動作能夠先行展現，提升互動的自然感。
REPORT_NEWS_AUDIO_TRIGGER_DELAY_SECONDS = 2.5
REPORT_NEWS_DELAY_CHARACTER_ID = "miku"
# 為了兼顧 VOAI 和 ElevenLabs 的 TTS 響應時間，並給予角色動作足夠的播放時間，設定揮手回應的語音觸發延遲為 2 秒。這樣可以確保在大多數情況下，角色的揮手動作能夠先行展現，提升互動的自然感。
WAVE_GREETING_AUDIO_TRIGGER_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class ActionBinding:
    name: str
    motion_key: str
    status_label: str
    handler_name: str
    skip_tts_sync: bool = False
    play_once: bool = False
    panel_loop: bool = False
    panel_muted: bool = True
    finish_event: str = "default"
    non_repeatable: bool = False
    blocks_following_dispatch: bool = False


@dataclass
class PendingActionState:
    trace_id: str
    binding: ActionBinding
    status: str = "pending"
    has_tts: bool = False
    timeout_timer: QTimer | None = None
    fallback_grace_applied: bool = False
    wait_for_tts_start: bool = False


@dataclass(frozen=True)
class DeferredDispatch:
    directive: str
    trace_id: str | None
    allow_tts: bool


class MotionCoordinator(QObject):
    """Presentation 的動畫、TTS 與播放收尾狀態機。"""

    def __init__(
        self,
        window: "TransparentWindow",
        library: "CharacterLibrary",
        tts_worker_factory=AdaptiveTTSFallbackWorker,
        news_worker_factory=NewsFetchWorker,
        music_worker_factory=MusicSelectionWorker,
        wave_worker_factory=WaveGreetingWorker,
        fixed_intent_worker_factory=FixedIntentReplyWorker,
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
            tts_worker_factory if callable(tts_worker_factory) else AdaptiveTTSFallbackWorker
        )
        self._news_worker_factory = (
            news_worker_factory if callable(news_worker_factory) else NewsFetchWorker
        )
        self._music_worker_factory = (
            music_worker_factory if callable(music_worker_factory) else MusicSelectionWorker
        )
        self._wave_worker_factory = (
            wave_worker_factory if callable(wave_worker_factory) else WaveGreetingWorker
        )
        self._fixed_intent_worker_factory = (
            fixed_intent_worker_factory if callable(fixed_intent_worker_factory) else FixedIntentReplyWorker
        )
        self._motion_path_resolver = motion_path_resolver
        self._tts_enabled = tts_enabled
        self._latency_tracker = latency_tracker
        self._active_tts_worker: object | None = None
        self._pending_tts_chunks: "queue.Queue[tuple[str, str, str | None]]" = queue.Queue()
        self._pending_actions: dict[str, PendingActionState] = {}
        self._suppressed_traces: set[str] = set()
        self._tts_not_expected_traces: set[str] = set()
        self._driver_started_replies: set[str] = set()
        self._driver_started_pairs: set[tuple[str, str]] = set()
        self._queued_playback_results: dict[str, tuple[str | None, str]] = {}
        self._trace_tts_providers: dict[str, str] = {}
        self._trace_pending_tts_counts: dict[str, int] = {}
        self._completed_tts_traces: set[str] = set()
        self._deferred_dispatches: deque[DeferredDispatch] = deque()
        self._active_action_trace_id: str | None = None
        self._action_sync_timeout_ms = max(500, int(getattr(config, "ACTION_SYNC_TIMEOUT_MS", 6000))) #VOAI Timeout 約 5-6 秒，ElevenLabs Timeout 約 3-4 秒，綜合考量後設定為 6 秒以兼顧兩者並留有緩衝
        self._fallback_timeout_grace_ms = 500
        self._current_loop_action_key: str | None = None
        self._current_loop_binding: ActionBinding | None = None
        self._loop_action_tts_queued: bool = False
        self._loop_cleanup_timer: QTimer | None = None
        self._news_audio_delay_timer: QTimer | None = None
        self._news_audio_trigger_delay_ms = max(0, int(REPORT_NEWS_AUDIO_TRIGGER_DELAY_SECONDS * 1000))
        self._wave_greeting_delay_timer: QTimer | None = None
        self._wave_greeting_audio_delay_ms = max(0, int(WAVE_GREETING_AUDIO_TRIGGER_DELAY_SECONDS * 1000))
        self._panel_video_ended: bool = False
        self._panel_video_started: bool = False
        self._wait_for_main_video_ended: bool = False
        self._wait_for_room_audio_ended: bool = False
        self._loop_action_service_pending: bool = False
        # AudioStreamWorker：Consumer，持續從佇列取出 BytesIO 並播放
        self._audio_worker = AudioStreamWorker(parent=self)
        self._audio_worker.driver_started.connect(self._on_audio_driver_started)
        self._audio_worker.playback_finished.connect(self._on_audio_playback_finished)
        self._audio_worker.queue_drained.connect(self._on_audio_queue_drained)
        self._audio_worker.start()
        self._bindings = {
            "report_news": ActionBinding(
                name="report_news",
                motion_key="report_news",
                status_label=REPLY_STATUS_LABEL,
                handler_name="_handle_report_news",
                skip_tts_sync=True,
                panel_loop=True,
                finish_event="room_audio",
                non_repeatable=True,
                blocks_following_dispatch=True,
            ),
            "play_music": ActionBinding(
                name="play_music",
                motion_key="play_music",
                status_label="正在挑選音樂",
                handler_name="_handle_play_music",
                skip_tts_sync=True,
                play_once=True,
                panel_muted=False,
                finish_event="panel_video",
                non_repeatable=True,
                blocks_following_dispatch=True,
            ),
            "wave_response": ActionBinding(
                name="wave_response",
                motion_key="wave_response",
                status_label="正在回應揮手",
                handler_name="_handle_wave_response",
                skip_tts_sync=True,
                finish_event="main_video",
                non_repeatable=True,
                blocks_following_dispatch=True,
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
            "cached_joke": ActionBinding(
                name="cached_joke",
                motion_key="laugh",
                status_label="正在分享笑話",
                handler_name="_handle_motion_only",
                finish_event="room_audio",
                non_repeatable=True,
                blocks_following_dispatch=True,
            ),
            "cached_share": ActionBinding(
                name="cached_share",
                motion_key="listen",
                status_label="正在分享內容",
                handler_name="_handle_motion_only",
                finish_event="room_audio",
                non_repeatable=True,
                blocks_following_dispatch=True,
            ),
        }

    @property
    def is_tts_busy(self) -> bool:
        return (
            self._active_tts_worker is not None
            or not self._pending_tts_chunks.empty()
            or self._audio_worker.is_busy()
        )

    @property
    def has_active_motion(self) -> bool:
        return self._current_loop_action_key is not None or bool(self._pending_actions)

    @property
    def audio_worker(self) -> AudioStreamWorker:
        return self._audio_worker

    def trigger_cached_intent(self, intent_name: str, source_label: str) -> bool:
        normalized_intent = str(intent_name or "").strip().lower()
        binding = self._bindings.get(f"cached_{normalized_intent}")
        if binding is None:
            self._window.set_action_status(f"未支援的固定意圖: {intent_name}", tone="warn", timeout_ms=3600)
            return False
        if self._current_loop_binding is not None or self._loop_action_service_pending or self.is_tts_busy:
            self._window.set_action_status("上一輪互動尚未完成，暫時無法觸發固定回覆。", tone="working", timeout_ms=2600)
            return False
        current_character_id = self._current_character_id()
        self._loop_action_service_pending = True
        self._window.set_action_status(binding.status_label, tone="working", timeout_ms=0)
        worker = self._create_service_worker(
            self._fixed_intent_worker_factory,
            parent=self,
            intent_name=normalized_intent,
            character_id=current_character_id,
        )
        self._start_worker(
            worker,
            lambda success, message, payload: self._on_fixed_intent_finished(
                binding,
                source_label,
                success,
                message,
                payload,
            ),
        )
        return True

    def dispatch(
        self,
        directive: str,
        trace_id: str | None = None,
        allow_tts: bool = True,
        wait_for_tts_start: bool = False,
    ) -> bool:
        raw_action_name, display_message = self._parse_directive(directive)
        action_name = config.canonicalize_host_action(raw_action_name)
        normalized_trace_id = str(trace_id or "").strip()
        if self._should_defer_dispatch(action_name, normalized_trace_id):
            self._deferred_dispatches.append(
                DeferredDispatch(
                    directive=directive,
                    trace_id=trace_id,
                    allow_tts=allow_tts,
                )
            )
            print(
                "[ECHOES] 提示: "
                f"loop action `{self._current_loop_action_key}` 尚未播完，已暫存後續任務。"
            )
            return True
        if raw_action_name and action_name and raw_action_name != action_name:
            print(f"[ECHOES] 提示: action alias `{raw_action_name}` 已正規化為 `{action_name}`。")

        if raw_action_name and not action_name and self._find_motion_path(raw_action_name):
            # harness skill 的 motion key(如 music_idle)不在 HOST_ACTION 白名單,
            # 但角色有對應動作檔:合成 motion-only binding 走同一套 TTS 同步/收尾
            # 機制,動畫維持到同輪 TTS 播畢(queue_drained)才回 idle。
            action_name = raw_action_name
            self._bindings.setdefault(
                action_name,
                ActionBinding(
                    name=action_name,
                    motion_key=action_name,
                    status_label=REPLY_STATUS_LABEL,
                    handler_name="_handle_motion_only",
                ),
            )

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
                self._show_brain_message(
                    display_message,
                    has_action=False,
                    trace_id=trace_id,
                    allow_tts=allow_tts,
                )
                return True

            print(f"[ECHOES] 警告: 收到空白或無效訊息: {directive}")
            self._window.set_action_status("收到空白或無效訊息", tone="warn", timeout_ms=2800)
            return False

        binding = self._bindings.get(action_name)
        if not binding:
            print(f"[ECHOES] 警告: action `{action_name}` 尚未綁定。")
            self._window.restore_idle_video()
            return False
        if self._is_duplicate_loop_action(binding):
            print(f"[ECHOES] 提示: action `{binding.name}` 已在進行中，略過重複觸發。")
            return True
        effective_display_message = "" if binding.name == "report_news" else display_message
        message_tone = "working"
        if effective_display_message:
            message_tone = self._resolve_message_tone(effective_display_message, has_action=True)
            timeout_ms = 4200 if message_tone == "warn" else 6000 if message_tone == "error" else 6500
            status_message = effective_display_message
            if binding.name == "listen" and message_tone == "working":
                status_message = REPLY_STATUS_LABEL
                timeout_ms = 0
            self._window.set_action_status(status_message, tone=message_tone, timeout_ms=timeout_ms)
        else:
            self._window.set_action_status(binding.status_label, tone="working")

        print(f"[ECHOES] Action tag 命中: {action_name} -> motion `{binding.motion_key}`")
        if self._latency_tracker is not None:
            self._latency_tracker.mark_action_dispatched(trace_id, action_name)
        use_pending_sync = bool(trace_id)
        intentional_tts_suppression = False
        if use_pending_sync:
            self._start_pending_action(
                trace_id,
                binding,
                wait_for_tts_start=wait_for_tts_start,
            )
            motion_found = True
            if binding.skip_tts_sync and normalized_trace_id:
                motion_found = self._activate_pending_action(normalized_trace_id, promoted=False)
                if motion_found:
                    self._suppressed_traces.add(normalized_trace_id)
                    self._tts_not_expected_traces.add(normalized_trace_id)
                    self._audio_worker.suppress_trace(normalized_trace_id)
                    intentional_tts_suppression = True
        else:
            motion_found = self._play_binding_motion(binding)
            if not motion_found:
                print(f"[ECHOES] 警告: action {action_name} 缺少對應動作，改以安全狀態執行。")
                self._window.restore_idle_video()

        getattr(self, binding.handler_name)(binding, motion_found)

        if effective_display_message:
            try:
                if allow_tts and (not binding.skip_tts_sync or bool(trace_id)):
                    self._synthesize_tts(effective_display_message, tone=message_tone, trace_id=trace_id)
            except Exception as exc:  # pragma: no cover - 防止 TTS 異常阻斷動作播放
                print(f"[ECHOES] 警告: TTS 背景啟動失敗，但動作已照常執行。({exc})")
            if intentional_tts_suppression and motion_found and self._current_loop_action_key is not None:
                self._schedule_non_tts_loop_cleanup(binding)
        elif motion_found and self._current_loop_action_key is not None:
            self._schedule_non_tts_loop_cleanup(binding)
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

    def _show_brain_message(
        self,
        message: str,
        has_action: bool,
        trace_id: str | None = None,
        allow_tts: bool = True,
    ):
        tone = self._resolve_message_tone(message, has_action)
        timeout_ms = 4200 if tone == "warn" else 6000 if tone == "error" else 6500
        status_message = message
        status_tone = tone
        if tone not in {"warn", "error"}:
            status_message = REPLY_STATUS_LABEL
            status_tone = "working"
            timeout_ms = 0
        self._window.set_action_status(status_message, tone=status_tone, timeout_ms=timeout_ms)
        if allow_tts:
            self._synthesize_tts(message, tone=tone, trace_id=trace_id)

    def speak_text(self, message: str, trace_id: str | None = None, has_action: bool = False):
        tone = self._resolve_message_tone(message, has_action)
        self._synthesize_tts(message, tone=tone, trace_id=trace_id)

    @staticmethod
    def _resolve_message_tone(message: str, has_action: bool) -> str:
        normalized = message.strip().lower()
        if normalized.startswith(("警告:", "[warn]", "warn:")) or "無法連線" in normalized or "連線已中斷" in normalized:
            return "warn"
        if normalized.startswith(("錯誤:", "[error]", "error:")):
            return "error"
        return "working" if has_action else "idle"

    def _is_duplicate_loop_action(self, binding: ActionBinding) -> bool:
        if not binding.non_repeatable:
            return False
        if self._current_loop_binding is not None and self._current_loop_binding.name == binding.name:
            return True
        return any(state.binding.name == binding.name for state in self._pending_actions.values())

    def _should_defer_dispatch(self, action_name: str | None, trace_id: str | None) -> bool:
        active_binding = self._current_loop_binding
        if active_binding is None or not active_binding.blocks_following_dispatch:
            return False
        normalized_trace_id = str(trace_id or "").strip()
        active_trace_id = str(self._active_action_trace_id or "").strip()
        if (
            action_name
            and action_name in {active_binding.name, active_binding.motion_key}
            and not normalized_trace_id
        ):
            return False
        if normalized_trace_id and (
            (active_trace_id and normalized_trace_id == active_trace_id)
            or normalized_trace_id in self._pending_actions
        ):
            return False
        if not normalized_trace_id and not action_name:
            return False
        return True

    def _handle_report_news(self, binding: ActionBinding, motion_found: bool):
        self._loop_action_service_pending = True
        current_character_id = self._current_character_id()
        if current_character_id:
            panel_path = self._call_library_method("get_panel_motion_path", current_character_id, "report_news")
            if panel_path and hasattr(self._window, "play_panel_video"):
                self._panel_video_started = True
                self._panel_video_ended = False
                self._window.play_panel_video(panel_path, muted=binding.panel_muted, loop=binding.panel_loop)
        worker = self._create_service_worker(
            self._news_worker_factory,
            parent=self,
            character_id=current_character_id,
        )
        self._start_worker(
            worker,
            lambda success, message, payload: self._on_news_finished(
                binding,
                motion_found,
                current_character_id,
                success,
                message,
                payload,
            ),
        )

    def _handle_play_music(self, binding: ActionBinding, motion_found: bool):
        if hasattr(self._window, "stop_music"):
            self._window.stop_music()
        current_character_id = self._current_character_id()
        panel_path = None
        if current_character_id:
            panel_path = self._call_library_method("get_panel_motion_path", current_character_id, "play_music")
        if panel_path and hasattr(self._window, "play_panel_video"):
            self._panel_video_started = True
            self._panel_video_ended = False
            self._window.play_panel_video(panel_path, muted=binding.panel_muted, loop=binding.panel_loop)
            self._window.set_action_status("正在播放面板音樂", tone="music")
            if motion_found and self._current_loop_action_key is not None:
                self._schedule_non_tts_loop_cleanup(binding)
            return

        self._loop_action_service_pending = True
        worker = self._music_worker_factory(parent=self)
        self._start_worker(worker, lambda success, message, payload: self._on_music_finished(binding, motion_found, success, message, payload))

    def _handle_wave_response(self, binding: ActionBinding, motion_found: bool):
        self._loop_action_service_pending = True
        if self._wave_greeting_audio_delay_ms <= 0 or QCoreApplication.instance() is None:
            self._start_wave_response_worker(binding, motion_found)
            return
        if self._wave_greeting_delay_timer is not None:
            self._wave_greeting_delay_timer.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._start_wave_response_worker(binding, motion_found))
        timer.start(self._wave_greeting_audio_delay_ms)
        self._wave_greeting_delay_timer = timer

    def _start_wave_response_worker(self, binding: ActionBinding, motion_found: bool):
        self._wave_greeting_delay_timer = None
        current_character_id = self._current_character_id()
        worker = self._create_service_worker(
            self._wave_worker_factory,
            parent=self,
            character_id=current_character_id,
        )
        self._start_worker(
            worker,
            lambda success, message, payload: self._on_wave_finished(
                binding,
                motion_found,
                success,
                message,
                payload,
            ),
        )

    def _schedule_report_news_audio_playback(
        self,
        binding: ActionBinding,
        audio_path: str,
        title: str,
        character_id: str | None,
    ):
        if self._news_audio_delay_timer is not None:
            self._news_audio_delay_timer.stop()
        delay_ms = self._report_news_audio_delay_ms_for_character(character_id)
        if delay_ms <= 0 or QCoreApplication.instance() is None:
            self._start_report_news_audio_playback(binding, audio_path, title)
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(
            lambda: self._start_report_news_audio_playback(
                binding,
                audio_path,
                title,
            )
        )
        timer.start(delay_ms)
        self._news_audio_delay_timer = timer

    def _report_news_audio_delay_ms_for_character(self, character_id: str | None) -> int:
        normalized_character_id = str(character_id or "").strip().lower()
        if normalized_character_id != REPORT_NEWS_DELAY_CHARACTER_ID:
            return 0
        return self._news_audio_trigger_delay_ms

    def _start_report_news_audio_playback(
        self,
        binding: ActionBinding,
        audio_path: str,
        title: str,
    ):
        self._news_audio_delay_timer = None
        if self._window.play_music(audio_path, title, update_status=False):
            self._arm_room_audio_wait()
            return
        self._schedule_non_tts_loop_cleanup(binding)

    def _handle_motion_only(self, binding: ActionBinding, motion_found: bool):
        if not motion_found:
            self._window.restore_idle_video()

    def _start_pending_action(
        self,
        trace_id: str | None,
        binding: ActionBinding,
        *,
        wait_for_tts_start: bool = False,
    ):
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        self._clear_pending_action(normalized_trace_id)
        self._suppressed_traces.discard(normalized_trace_id)
        self._tts_not_expected_traces.discard(normalized_trace_id)
        self._audio_worker.clear_suppressed_trace(normalized_trace_id)
        if hasattr(self._window, "stop_motion_loop"):
            self._window.stop_motion_loop()
        self._window.restore_idle_video()
        state = PendingActionState(
            trace_id=normalized_trace_id,
            binding=binding,
            wait_for_tts_start=wait_for_tts_start,
        )
        self._pending_actions[normalized_trace_id] = state
        # 同步模式只在實際 audio driver 起播後播放 WebM，避免 provider 慢時
        # timeout 先播完動作；TTS 失敗則保留文字並維持 idle。
        if wait_for_tts_start or QCoreApplication.instance() is None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda current_trace_id=normalized_trace_id: self._promote_pending_action(current_trace_id))
        timer.start(self._action_sync_timeout_ms)
        state.timeout_timer = timer

    def _clear_pending_action(self, trace_id: str | None):
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        state = self._pending_actions.pop(normalized_trace_id, None)
        if state is not None and state.timeout_timer is not None:
            state.timeout_timer.stop()
            state.timeout_timer = None

    def _activate_pending_action(self, trace_id: str | None, promoted: bool = False) -> bool:
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return False
        state = self._pending_actions.get(normalized_trace_id)
        if state is None:
            return False
        if state.timeout_timer is not None:
            state.timeout_timer.stop()
            state.timeout_timer = None
        if state.status == "timeout_promoted" and not promoted:
            return False
        state.status = "timeout_promoted" if promoted else "synced"
        self._active_action_trace_id = normalized_trace_id
        motion_found = self._play_binding_motion(state.binding)
        if not motion_found:
            print(f"[ECHOES] 警告: action {state.binding.name} 缺少對應動作，改以安全狀態執行。")
            self._window.restore_idle_video()
            self._clear_pending_action(normalized_trace_id)
            return False
        if state.has_tts:
            self._loop_action_tts_queued = True
        if promoted:
            if self._latency_tracker is not None:
                self._latency_tracker.mark_timeout_promoted(normalized_trace_id, state.binding.name)
            self._suppressed_traces.add(normalized_trace_id)
            self._audio_worker.suppress_trace(normalized_trace_id)
            self._schedule_loop_cleanup(2200)
        return True

    def _promote_pending_action(self, trace_id: str | None):
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        state = self._pending_actions.get(normalized_trace_id)
        if state is None or state.status != "pending":
            return
        self._activate_pending_action(normalized_trace_id, promoted=True)

    def _schedule_non_tts_loop_cleanup(self, binding: ActionBinding):
        if self._loop_action_service_pending or self._wait_for_room_audio_ended:
            return
        if binding.finish_event == "panel_video":
            self._schedule_loop_cleanup(180000)
            return
        if binding.finish_event == "main_video":
            self._wait_for_main_video_ended = True
            self._schedule_loop_cleanup(12000, wait_for_main_video_end=True)
            return
        self._schedule_loop_cleanup(12000 if self._wait_for_main_video_ended else 3000)

    def _render_synthetic_turn(self, source_label: str, assistant_text: str):
        show_synthetic_turn = getattr(self._window, "show_synthetic_conversation_turn", None)
        if callable(show_synthetic_turn):
            show_synthetic_turn("Dev Query", source_label, assistant_text)
            return
        trace_id = uuid4().hex
        begin_turn = getattr(self._window, "begin_conversation_turn", None)
        set_assistant = getattr(self._window, "set_conversation_assistant", None)
        finish_turn = getattr(self._window, "finish_conversation_turn", None)
        if callable(begin_turn):
            begin_turn(trace_id, "Dev Query", source_label)
        if callable(set_assistant):
            set_assistant(trace_id, assistant_text)
        if callable(finish_turn):
            finish_turn(trace_id)

    def _play_binding_motion(self, binding: ActionBinding) -> bool:
        motion_path, used_idle_fallback = self._resolve_action_motion_path(binding.motion_key)
        if not motion_path:
            self._current_loop_action_key = None
            self._current_loop_binding = None
            self._wait_for_main_video_ended = False
            self._wait_for_room_audio_ended = False
            self._loop_action_service_pending = False
            return False

        if used_idle_fallback:
            # 找不到對應動作，退回 idle，不視為 loop action
            self._current_loop_action_key = None
            self._current_loop_binding = None
            self._wait_for_main_video_ended = False
            self._wait_for_room_audio_ended = False
            self._loop_action_service_pending = False
            if hasattr(self._window, "play_resolved_motion"):
                return bool(self._window.play_resolved_motion(binding.motion_key, motion_path, loop=True))
            if hasattr(self._window, "change_video"):
                return bool(self._window.change_video(motion_path, loop=True))
            return bool(self._window.play_action_motion(binding.motion_key))

        # 所有真實動作統一使用 start_motion_loop（循環到明確停止為止）
        self._current_loop_action_key = binding.motion_key
        self._current_loop_binding = binding
        self._loop_action_tts_queued = False
        self._wait_for_main_video_ended = False
        self._wait_for_room_audio_ended = False
        self._panel_video_started = False
        self._panel_video_ended = False
        if binding.play_once and hasattr(self._window, "play_resolved_motion"):
            return bool(self._window.play_resolved_motion(binding.motion_key, motion_path, loop=False))
        if binding.play_once and hasattr(self._window, "change_video"):
            return bool(self._window.change_video(motion_path, loop=False))
        if hasattr(self._window, "start_motion_loop"):
            self._window.start_motion_loop(motion_path, 300)
            return True
        # fallback
        if hasattr(self._window, "play_resolved_motion"):
            return bool(self._window.play_resolved_motion(binding.motion_key, motion_path, loop=True))
        if hasattr(self._window, "change_video"):
            return bool(self._window.change_video(motion_path, loop=True))
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

        current_character_id = self._current_character_id()
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

    def _current_character_id(self) -> str | None:
        """active character 唯一來源是 window 背後的 router snapshot,不讀持久化 UI 狀態。"""
        getter = getattr(self._window, "get_current_character_id", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception as exc:  # noqa: BLE001 - 與 _call_library_method 相同的防禦策略
            print(f"[ECHOES] 警告: 取得 active character 失敗: {exc}")
            return None

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

    @staticmethod
    def _create_service_worker(factory, **kwargs):
        try:
            signature = inspect.signature(factory)
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if accepts_kwargs:
                accepted_kwargs = kwargs
            else:
                accepted_kwargs = {
                    name: value
                    for name, value in kwargs.items()
                    if name in signature.parameters
                }
            return factory(**accepted_kwargs)
        except (TypeError, ValueError):
            return factory(parent=kwargs.get("parent"))

    def _synthesize_tts(self, message: str, tone: str, trace_id: str | None = None):
        if not self._tts_enabled or tone in {"warn", "error"}:
            return

        speech_text = sanitize_tts_text(message)
        if not speech_text:
            return

        normalized_trace_id = str(trace_id or "").strip()
        if normalized_trace_id in self._suppressed_traces:
            skipped_by_design = normalized_trace_id in self._tts_not_expected_traces
            suppressed_message = (
                "因 play_music fast path 依設計略過語音。"
                if skipped_by_design
                else "因 timeout_promoted 已抑制後續句段。"
            )
            reply_id = uuid4().hex
            self._on_tts_finished(
                reply_id,
                False,
                suppressed_message,
                {
                    "reply_id": reply_id,
                    "trace_id": normalized_trace_id,
                    "text": speech_text,
                    "suppressed": True,
                    "tts_skipped_by_design": skipped_by_design,
                },
            )
            return

        if not callable(self._tts_worker_factory):
            print("[ECHOES] 警告: TTS worker factory 無效，已回退到 AdaptiveTTSFallbackWorker。")
            self._tts_worker_factory = AdaptiveTTSFallbackWorker

        reply_id = uuid4().hex
        self._pending_tts_chunks.put((reply_id, speech_text, trace_id))
        if normalized_trace_id:
            self._trace_pending_tts_counts[normalized_trace_id] = (
                self._trace_pending_tts_counts.get(normalized_trace_id, 0) + 1
            )
        pending_state = self._pending_actions.get(normalized_trace_id) if normalized_trace_id else None
        if pending_state is not None:
            pending_state.has_tts = True
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
            return

        reply_id, speech_text, trace_id = self._pending_tts_chunks.get_nowait()
        normalized_trace_id = str(trace_id or "").strip()
        if normalized_trace_id in self._suppressed_traces:
            skipped_by_design = normalized_trace_id in self._tts_not_expected_traces
            suppressed_message = (
                "因 play_music fast path 依設計略過語音。"
                if skipped_by_design
                else "因已改為文字-only 或 timeout_promoted，略過後續句段。"
            )
            self._on_tts_finished(
                reply_id,
                False,
                suppressed_message,
                {
                    "reply_id": reply_id,
                    "trace_id": normalized_trace_id,
                    "text": speech_text,
                    "suppressed": True,
                    "tts_skipped_by_design": skipped_by_design,
                },
            )
            self._start_next_tts_worker()
            return

        current_character_id = self._current_character_id()
        factory_name = getattr(self._tts_worker_factory, "__name__", "")
        preferred_provider = self._trace_tts_providers.get(normalized_trace_id, "")
        if factory_name in {"VoAIStreamingTTSWorker", "AdaptiveTTSFallbackWorker"}:
            voice_id = current_character_id or ""
        else:
            voice_id = config.get_elevenlabs_voice_id_for_character(current_character_id)

        worker_kwargs = {
            "text": speech_text,
            "reply_id": reply_id,
            "trace_id": trace_id,
            "voice_id": voice_id,
            "parent": self,
        }
        try:
            signature = inspect.signature(self._tts_worker_factory)
            if "playback_guard" in signature.parameters:
                worker_kwargs["playback_guard"] = self._can_start_trace_audio
            if "fallback_voice_id" in signature.parameters:
                worker_kwargs["fallback_voice_id"] = config.get_elevenlabs_voice_id_for_character(current_character_id)
            if "preferred_provider" in signature.parameters and preferred_provider:
                worker_kwargs["preferred_provider"] = preferred_provider
            if "resolved_tts_mode" in signature.parameters:
                resolved_mode, _ = config.resolve_tts_runtime_mode()
                worker_kwargs["resolved_tts_mode"] = resolved_mode
            if "pcm_stream_sink" in signature.parameters:
                worker_kwargs["pcm_stream_sink"] = self._audio_worker
        except (TypeError, ValueError):
            pass

        worker = self._tts_worker_factory(**worker_kwargs)
        self._active_tts_worker = worker
        self._workers.append(worker)

        def handle_audio_ready(audio_bytes, r_id: str, t_id: str):
            if t_id and t_id in self._suppressed_traces:
                return
            self._audio_worker.enqueue(audio_bytes, r_id, t_id)

        def handle_result(success: bool, result_message: str, payload: object, current_reply_id=reply_id):
            self._on_tts_finished(current_reply_id, success, result_message, payload)

        def handle_progress(event_name: str, payload: object):
            self._on_tts_progress(event_name, payload)

        def on_thread_finished(current_worker=worker):
            # HTTP 取音訊完成（非播放完成），立即啟動下一個 HTTP 取音訊
            # AudioStreamWorker 負責按序播放，不再等待音訊播完才取下一句
            if current_worker in self._workers:
                self._workers.remove(current_worker)
            if self._active_tts_worker is current_worker:
                self._active_tts_worker = None
            if hasattr(current_worker, "deleteLater"):
                current_worker.deleteLater()
            self._start_next_tts_worker()
            self._finish_loop_action_if_tts_idle()

        if hasattr(worker, "audio_ready_signal"):
            worker.audio_ready_signal.connect(handle_audio_ready)
        worker.finished_signal.connect(handle_result)
        if hasattr(worker, "progress_signal"):
            worker.progress_signal.connect(handle_progress)
        worker.finished.connect(on_thread_finished)
        worker.start()

    def _on_audio_queue_drained(self):
        """AudioStreamWorker 播放佇列清空時觸發。用於 loop action 的 TTS 完成偵測。"""
        self._finish_loop_action_if_tts_idle()

    def _maybe_close_trace_audio_session(self, trace_id: str | None):
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id or normalized_trace_id not in self._completed_tts_traces:
            return
        if self._trace_pending_tts_counts.get(normalized_trace_id, 0) > 0:
            return
        self._audio_worker.close_trace_session(normalized_trace_id)

    def _finish_loop_action_if_tts_idle(self):
        if not (self._loop_action_tts_queued
                and self._current_loop_action_key is not None
                and self._pending_tts_chunks.empty()
                and self._active_tts_worker is None
                and not self._audio_worker.is_busy()):
            return
        if self._wait_for_room_audio_ended or self._loop_action_service_pending:
            return
        current_binding = self._current_loop_binding
        if current_binding is None:
            self._finish_loop_action()
            return
        # panel video 尚未結束時，等待 JS 的 _on_panel_video_ended 回調再 finish
        if self._panel_video_started and not self._panel_video_ended:
            return
        if current_binding.finish_event == "main_video":
            self._wait_for_main_video_ended = True
            self._schedule_loop_cleanup(12000, wait_for_main_video_end=True)
            return
        self._finish_loop_action()

    def _on_panel_video_ended(self):
        """JS panel video 播畢時的 Python 回調（由 transparent_window 轉發）。"""
        self._panel_video_ended = True
        if self._current_loop_action_key is None:
            return
        if self._wait_for_room_audio_ended or self._loop_action_service_pending:
            return
        current_binding = self._current_loop_binding
        if current_binding is None:
            self._finish_loop_action()
            return
        tts_idle = (
            not self._loop_action_tts_queued
            or (self._pending_tts_chunks.empty()
                and self._active_tts_worker is None
                and not self._audio_worker.is_busy())
        )
        if tts_idle:
            if current_binding.finish_event == "panel_video":
                self._finish_loop_action()
                return
            if current_binding.finish_event == "main_video":
                self._wait_for_main_video_ended = True
                self._schedule_loop_cleanup(12000, wait_for_main_video_end=True)
                return
            self._finish_loop_action()

    def _on_main_video_ended(self):
        """JS main character video 播畢時的 Python 回調。"""
        if self._wait_for_room_audio_ended or self._loop_action_service_pending:
            return
        if not (self._wait_for_main_video_ended and self._current_loop_action_key is not None):
            return
        self._finish_loop_action()

    def _on_room_audio_ended(self):
        if not (self._wait_for_room_audio_ended and self._current_loop_action_key is not None):
            return
        self._wait_for_room_audio_ended = False
        current_binding = self._current_loop_binding
        if current_binding is not None and current_binding.finish_event == "main_video":
            self._wait_for_main_video_ended = True
            self._schedule_loop_cleanup(12000, wait_for_main_video_end=True)
            return
        self._finish_loop_action()

    def _on_tts_progress(self, event_name: str, payload: object):
        if not isinstance(payload, dict):
            return
        normalized_trace_id = str(payload.get("trace_id") or "").strip()
        if event_name == "provider_selected":
            provider = str(payload.get("provider") or "").strip()
            if normalized_trace_id and provider:
                self._trace_tts_providers[normalized_trace_id] = provider
            if self._latency_tracker is not None:
                self._latency_tracker.mark_tts_provider_selected(
                    normalized_trace_id,
                    provider,
                    str(payload.get("reason") or ""),
                )
            if payload.get("fallback_locked"):
                self._apply_fallback_timeout_grace(normalized_trace_id)
            return
        if event_name == "fallback_triggered":
            target_provider = str(payload.get("to_provider") or "elevenlabs").strip() or "elevenlabs"
            if normalized_trace_id:
                self._trace_tts_providers[normalized_trace_id] = target_provider
            if self._latency_tracker is not None:
                self._latency_tracker.mark_tts_fallback_triggered(
                    normalized_trace_id,
                    str(payload.get("from_provider") or "voai"),
                    target_provider,
                    str(payload.get("failure_code") or ""),
                )
            self._apply_fallback_timeout_grace(normalized_trace_id)
            return
        if event_name == "critical_tts_failure":
            if self._latency_tracker is not None:
                self._latency_tracker.mark_text_only_completed(
                    normalized_trace_id,
                    str(payload.get("provider_chain") or ""),
                )
            return
        if event_name == "stream_started" and self._latency_tracker is not None:
            self._latency_tracker.mark_tts_stream_started(
                normalized_trace_id,
                str(payload.get("reply_id", "")),
                int(payload.get("bytes_forwarded", payload.get("bytes_received", 0)) or 0),
            )
            return
        if event_name in {"driver_started", "playback_started"}:
            self._on_driver_started(
                str(payload.get("reply_id", "")),
                payload.get("trace_id"),
            )

    def _on_audio_driver_started(self, reply_id: str, trace_id: str):
        self._on_driver_started(reply_id, trace_id)

    def _on_audio_playback_finished(self, reply_id: str, trace_id: str):
        result = self._queued_playback_results.pop(reply_id, None)
        message = result[1] if result is not None else "語音播放完成。"
        normalized_trace_id = str(trace_id or "").strip()
        if self._latency_tracker is not None:
            self._latency_tracker.mark_tts_finished(normalized_trace_id, reply_id, True, message)
        print(f"[ECHOES] 提示: 語音播放完成。{message}")

    def _on_driver_started(self, reply_id: str, trace_id: str | None):
        if not reply_id:
            return
        normalized_trace_id = str(trace_id or "").strip()
        event_key = (reply_id, normalized_trace_id)
        if event_key in self._driver_started_pairs:
            return
        self._driver_started_pairs.add(event_key)
        self._driver_started_replies.add(reply_id)
        if self._latency_tracker is not None:
            self._latency_tracker.mark_driver_started(normalized_trace_id, reply_id)
        if normalized_trace_id in self._suppressed_traces:
            return
        self._activate_pending_action(normalized_trace_id, promoted=False)

    def reset_runtime_state(self):
        for trace_id in list(self._pending_actions):
            self._clear_pending_action(trace_id)
        self._pending_actions.clear()
        self._suppressed_traces.clear()
        self._tts_not_expected_traces.clear()
        self._driver_started_replies.clear()
        self._driver_started_pairs.clear()
        self._queued_playback_results.clear()
        self._trace_tts_providers.clear()
        self._trace_pending_tts_counts.clear()
        self._completed_tts_traces.clear()
        self._deferred_dispatches.clear()
        while not self._pending_tts_chunks.empty():
            try:
                self._pending_tts_chunks.get_nowait()
            except queue.Empty:
                break
        if self._loop_cleanup_timer is not None:
            self._loop_cleanup_timer.stop()
            self._loop_cleanup_timer = None
        self._audio_worker.clear_queue()
        active_worker = self._active_tts_worker
        if active_worker is not None and hasattr(active_worker, "quit"):
            try:
                active_worker.quit()
            except Exception:
                pass
        self._active_tts_worker = None
        self._current_loop_action_key = None
        self._current_loop_binding = None
        self._loop_action_tts_queued = False
        self._active_action_trace_id = None
        self._wait_for_main_video_ended = False
        self._wait_for_room_audio_ended = False
        self._loop_action_service_pending = False
        self._panel_video_started = False
        self._panel_video_ended = False

    def _on_tts_finished(self, reply_id: str, success: bool, message: str, payload: object):
        trace_id = payload.get("trace_id") if isinstance(payload, dict) else None
        normalized_trace_id = str(trace_id or "").strip()
        skipped_by_design = bool(isinstance(payload, dict) and payload.get("tts_skipped_by_design"))
        if normalized_trace_id:
            pending_count = self._trace_pending_tts_counts.get(normalized_trace_id, 0)
            if pending_count <= 1:
                self._trace_pending_tts_counts.pop(normalized_trace_id, None)
            else:
                self._trace_pending_tts_counts[normalized_trace_id] = pending_count - 1
            # 標記同輪最後一個 TTS producer 已完成；後續各結果分支會統一
            # 關閉 PCM session。session 關閉後 AudioStreamWorker 才會發出
            # queue_drained，讓角色動作回到 idle。
            if self._trace_pending_tts_counts.get(normalized_trace_id, 0) == 0:
                self._completed_tts_traces.add(normalized_trace_id)
        if normalized_trace_id in self._suppressed_traces and reply_id not in self._driver_started_replies and not skipped_by_design:
            success = False
            if "抑制" not in message:
                message = "因 timeout_promoted 抑制晚到音訊。"
        if isinstance(payload, dict):
            selected_provider = str(payload.get("selected_provider") or payload.get("provider") or "").strip()
            if normalized_trace_id and selected_provider:
                self._trace_tts_providers[normalized_trace_id] = selected_provider
            if payload.get("critical_tts_failure"):
                self._handle_critical_tts_failure(normalized_trace_id, message)
        if skipped_by_design and self._latency_tracker is not None:
            self._latency_tracker.mark_tts_skipped_by_design(normalized_trace_id, message)
        queued_playback = bool(isinstance(payload, dict) and payload.get("queued_playback"))
        if queued_playback and success:
            self._queued_playback_results[reply_id] = (trace_id, message)
            self._maybe_close_trace_audio_session(normalized_trace_id)
            return
        if self._latency_tracker is not None:
            self._latency_tracker.mark_tts_finished(
                trace_id,
                reply_id,
                success,
                message,
                skipped_by_design=skipped_by_design,
            )
        if not success and not skipped_by_design:
            print(f"[ECHOES] 提示: 串流 TTS 未播放，保留文字回覆。{message}")
            pending_state = self._pending_actions.get(normalized_trace_id)
            if (
                pending_state is not None
                and pending_state.wait_for_tts_start
                and self._trace_pending_tts_counts.get(normalized_trace_id, 0) == 0
            ):
                self._clear_pending_action(normalized_trace_id)
                self._window.restore_idle_video()
            self._maybe_close_trace_audio_session(normalized_trace_id)
            return
        print(f"[ECHOES] 提示: 語音播放完成。{message}")
        self._maybe_close_trace_audio_session(normalized_trace_id)

    def _apply_fallback_timeout_grace(self, trace_id: str | None):
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        state = self._pending_actions.get(normalized_trace_id)
        if state is None or state.status != "pending" or state.fallback_grace_applied:
            return
        timer = state.timeout_timer
        if timer is None:
            return
        remaining_ms = max(0, int(timer.remainingTime()))
        timer.start(remaining_ms + self._fallback_timeout_grace_ms)
        state.fallback_grace_applied = True

    def _handle_critical_tts_failure(self, trace_id: str | None, message: str):
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        self._suppressed_traces.add(normalized_trace_id)
        self._audio_worker.suppress_trace(normalized_trace_id)
        self._clear_pending_action(normalized_trace_id)
        if self._active_action_trace_id == normalized_trace_id and self._current_loop_action_key is not None:
            self._finish_loop_action()
            self._suppressed_traces.add(normalized_trace_id)
            self._audio_worker.suppress_trace(normalized_trace_id)
        else:
            self._window.restore_idle_video()
        self._window.set_action_status(
            message or "語音服務暫時失敗，已保留文字回覆。",
            tone="warn",
            timeout_ms=4200,
        )

    def _can_start_trace_audio(self, trace_id: str | None, _reply_id: str | None = None) -> bool:
        normalized_trace_id = str(trace_id or "").strip()
        return normalized_trace_id not in self._suppressed_traces

    def shutdown(self, wait_ms: int = 5000):
        if self._news_audio_delay_timer is not None:
            self._news_audio_delay_timer.stop()
            self._news_audio_delay_timer = None
        if self._wave_greeting_delay_timer is not None:
            self._wave_greeting_delay_timer.stop()
            self._wave_greeting_delay_timer = None
        for trace_id in list(self._pending_actions):
            self._clear_pending_action(trace_id)
        self._pending_actions.clear()
        self._suppressed_traces.clear()
        self._tts_not_expected_traces.clear()
        self._driver_started_replies.clear()
        self._driver_started_pairs.clear()
        self._queued_playback_results.clear()
        self._trace_tts_providers.clear()
        self._trace_pending_tts_counts.clear()
        self._completed_tts_traces.clear()
        self._deferred_dispatches.clear()
        while not self._pending_tts_chunks.empty():
            try:
                self._pending_tts_chunks.get_nowait()
            except queue.Empty:
                break

        # 停止 AudioStreamWorker（清空佇列後送 sentinel 正常退出）
        self._audio_worker.clear_queue()
        self._audio_worker.stop()
        self._audio_worker.wait(wait_ms)

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
        self._current_loop_binding = None
        self._workers = []

    def _on_news_finished(
        self,
        binding: ActionBinding,
        motion_found: bool,
        character_id: str | None,
        success: bool,
        message: str,
        payload: object,
    ):
        del motion_found
        self._loop_action_service_pending = False
        if success:
            if isinstance(payload, dict) and payload.get("path"):
                title = str(payload.get("title") or "固定新聞播報")
                self._schedule_report_news_audio_playback(
                    binding,
                    str(payload.get("path") or ""),
                    title,
                    character_id,
                )
                return
            self._schedule_non_tts_loop_cleanup(binding)
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
        self._loop_action_service_pending = False
        has_audio = False
        if success and isinstance(payload, dict):
            if self._window.play_music(payload.get("path", ""), payload.get("title", "")):
                self._window.set_action_status(f"正在播放: {payload.get('title', message)}", tone="music")
                self._arm_room_audio_wait()
                has_audio = True

        if not has_audio:
            self._window.stop_music()
            if self._panel_video_started and hasattr(self._window, "set_panel_video_muted"):
                self._window.set_panel_video_muted(False)
                fallback_title = payload.get("title") if isinstance(payload, dict) else ""
                if fallback_title:
                    self._window.set_action_status(f"正在播放: {fallback_title}", tone="music")
                else:
                    self._window.set_action_status("正在播放面板音訊", tone="music")
                self._schedule_non_tts_loop_cleanup(binding)
                return
            print(f"[ECHOES] 提示: 無可播放音樂（{message}），動畫繼續顯示。")
            self._window.set_action_status("音樂播放中", tone="music")
            self._schedule_non_tts_loop_cleanup(binding)

    def _on_wave_finished(
        self,
        binding: ActionBinding,
        motion_found: bool,
        success: bool,
        message: str,
        payload: object,
    ):
        self._loop_action_service_pending = False
        if success and isinstance(payload, dict) and payload.get("path"):
            if self._window.play_music(
                str(payload.get("path") or ""),
                str(payload.get("title") or "嗨 你好嗎"),
                update_status=False,
            ):
                self._arm_room_audio_wait(timeout_ms=15000)
                return
        self._handle_failure(binding, motion_found, message)

    def _on_fixed_intent_finished(
        self,
        binding: ActionBinding,
        source_label: str,
        success: bool,
        message: str,
        payload: object,
    ):
        self._loop_action_service_pending = False
        if not success or not isinstance(payload, dict):
            self._window.set_action_status(message, tone="error", timeout_ms=4800)
            return
        display_text = str(payload.get("text") or "").strip()
        audio_path = str(payload.get("path") or payload.get("audio_path") or "").strip()
        title = str(payload.get("title") or display_text or binding.status_label)
        if not display_text or not audio_path:
            self._window.set_action_status("固定回覆快取資料不完整。", tone="error", timeout_ms=4800)
            return
        display_source_label = resolve_fixed_intent_source_label(
            str(payload.get("intent") or binding.name.replace("cached_", "")),
            source_label,
        )
        self._render_synthetic_turn(display_source_label, display_text)
        self._window.set_action_status(display_text, tone="working", timeout_ms=6500)
        motion_found = self._play_binding_motion(binding)
        if not motion_found:
            self._window.restore_idle_video()
            self._window.set_action_status("找不到對應動作，已略過固定回覆播放。", tone="warn", timeout_ms=4200)
            return
        if self._window.play_music(audio_path, title, update_status=False):
            self._arm_room_audio_wait(timeout_ms=15000)
            return
        self._handle_failure(binding, motion_found, f"{title} 音檔播放失敗。")

    def _arm_room_audio_wait(self, timeout_ms: int = 180000):
        if self._current_loop_action_key is None:
            return
        self._wait_for_room_audio_ended = True
        self._wait_for_main_video_ended = False
        self._schedule_loop_cleanup(timeout_ms)

    def _schedule_loop_cleanup(self, delay_ms: int = 8000, wait_for_main_video_end: bool = False):
        if QCoreApplication.instance() is None:
            return
        if self._loop_cleanup_timer is not None:
            self._loop_cleanup_timer.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        if wait_for_main_video_end:
            timer.timeout.connect(self._on_loop_cleanup_timeout)
        else:
            timer.timeout.connect(self._finish_loop_action)
        timer.start(delay_ms)
        self._loop_cleanup_timer = timer

    def _on_loop_cleanup_timeout(self):
        if self._current_loop_action_key is None:
            return
        if self._wait_for_main_video_ended:
            print(
                "[ECHOES] 警告: "
                f"loop action `{self._current_loop_action_key}` 等不到 main video ended，改用保護性 cleanup。"
            )
        self._finish_loop_action()

    def _finish_loop_action(self):
        if self._current_loop_action_key is None:
            return
        print(f"[ECHOES] loop action '{self._current_loop_action_key}' 完成，清理動畫")
        if self._loop_cleanup_timer is not None:
            self._loop_cleanup_timer.stop()
            self._loop_cleanup_timer = None
        if self._news_audio_delay_timer is not None:
            self._news_audio_delay_timer.stop()
            self._news_audio_delay_timer = None
        if self._wave_greeting_delay_timer is not None:
            self._wave_greeting_delay_timer.stop()
            self._wave_greeting_delay_timer = None
        self._current_loop_action_key = None
        self._current_loop_binding = None
        self._loop_action_tts_queued = False
        self._wait_for_main_video_ended = False
        self._wait_for_room_audio_ended = False
        self._loop_action_service_pending = False
        self._panel_video_started = False
        self._panel_video_ended = False
        if self._active_action_trace_id:
            self._clear_pending_action(self._active_action_trace_id)
            self._suppressed_traces.discard(self._active_action_trace_id)
            self._tts_not_expected_traces.discard(self._active_action_trace_id)
            self._audio_worker.clear_suppressed_trace(self._active_action_trace_id)
            self._active_action_trace_id = None
        if hasattr(self._window, "stop_motion_loop"):
            self._window.stop_motion_loop()
        if hasattr(self._window, "clear_panel_video"):
            self._window.clear_panel_video()
        self._window.restore_idle_video()
        self._drain_deferred_dispatches()

    def _drain_deferred_dispatches(self):
        while self._deferred_dispatches and self._current_loop_action_key is None:
            pending = self._deferred_dispatches.popleft()
            print("[ECHOES] 提示: 已恢復執行先前暫存的任務。")
            self.dispatch(
                pending.directive,
                trace_id=pending.trace_id,
                allow_tts=pending.allow_tts,
            )
            if self._current_loop_binding is not None and self._current_loop_binding.blocks_following_dispatch:
                return

    def _handle_failure(self, binding: ActionBinding, motion_found: bool, message: str):
        print(f"[ECHOES] 警告: action {binding.name} 執行失敗: {message}")
        self._finish_loop_action()
        self._window.set_action_status(message, tone="error", timeout_ms=6000)
        self._window.restore_idle_video()
