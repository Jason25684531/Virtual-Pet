"""SttController — 錄音 session 狀態機、背景 worker、與既有 UI 文字入口的一次性提交橋接。

只負責 session／狀態／提交協調，不載入模型、不操作 UI 元件、不 import Harness。
所有狀態轉換集中於本類別，實際錄音與推論在背景 daemon thread 執行，結果一律經
pyqtSignal 送回 UI thread；由呼叫方（UI 組裝層）連接 transcript_ready 到既有
TransparentWindow.submit_agentic_text()。
"""

from __future__ import annotations

import re
import threading
import logging
from enum import Enum
from time import perf_counter

from PyQt5.QtCore import QObject, pyqtSignal

from sensors.faster_whisper_stt import SttError
from sensors.microphone_recorder import MicrophoneError, MicrophoneRecorder

_PUNCTUATION_ONLY_RE = re.compile(r"^[\s\W_]*$", re.UNICODE)
LOGGER = logging.getLogger(__name__)


class RecordingState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    TRANSCRIBING = "transcribing"
    SUBMITTING = "submitting"
    ERROR = "error"


def _is_effectively_empty(text: str) -> bool:
    return _PUNCTUATION_ONLY_RE.match(text) is not None


class SttController(QObject):
    """UI thread 上的狀態機 orchestrator；錄音／推論均在背景 daemon thread 執行。"""

    state_changed = pyqtSignal(str)
    transcript_ready = pyqtSignal(str)
    # Raw perf_counter() floats for the turn latency timeline: is_vad_endpoint,
    # vad_endpoint_ts (unused/0.0 when not vad-triggered), stt_started_ts, stt_done_ts.
    # Kept as plain floats, not a timeline object, so this module stays dependency-free
    # per module-dependency-boundaries; the receiving adapter owns the timeline object.
    voice_turn_timing = pyqtSignal(bool, float, float, float)
    session_discarded = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    availability_changed = pyqtSignal(bool)

    def __init__(
        self,
        recorder: MicrophoneRecorder,
        provider,
        min_recording_ms: int,
        sample_rate: int,
        vad: object | None = None,
    ) -> None:
        super().__init__()
        self._recorder = recorder
        self._provider = provider
        self._min_recording_ms = int(min_recording_ms)
        self._sample_rate = int(sample_rate)
        self._vad = vad
        self._state = RecordingState.IDLE
        self._state_lock = threading.Lock()
        self._session_thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._submitted = False
        self._session_id = 0
        self._last_error = ""
        self._shutting_down = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state.value

    @property
    def is_listening(self) -> bool:
        return self._state == RecordingState.RECORDING

    @property
    def last_error(self) -> str:
        return self._last_error

    def start_session(self) -> None:
        """僅在 IDLE 接受；非 IDLE（含 transcribing）一律 no-op。"""
        with self._state_lock:
            if self._shutting_down or self._state != RecordingState.IDLE:
                return
            self._session_id += 1
            session_id = self._session_id
            self._submitted = False
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._state = RecordingState.STARTING
        self.state_changed.emit(RecordingState.STARTING.value)

        thread = threading.Thread(
            target=self._run_session,
            args=(session_id, stop_event),
            daemon=True,
            name=f"SttSession-{session_id}",
        )
        with self._state_lock:
            self._session_thread = thread
        thread.start()

    def stop_session(self) -> None:
        """僅在 RECORDING 接受，其餘（含 transcribing）一律 no-op。"""
        with self._state_lock:
            if self._state != RecordingState.RECORDING:
                return
            stop_event = self._stop_event
            self._state = RecordingState.STOPPING
        self.state_changed.emit(RecordingState.STOPPING.value)
        if stop_event is not None:
            stop_event.set()

    def preload_model(self) -> None:
        """背景載入模型；呼叫方（main.py）只在 STT_ENABLED=true 時呼叫。"""
        threading.Thread(target=self._preload, daemon=True, name="SttPreload").start()

    def shutdown(self) -> None:
        """停止進行中 session、關閉 stream、釋放模型；冪等、不阻塞退出流程。"""
        with self._state_lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            stop_event = self._stop_event
            thread = self._session_thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        try:
            self._recorder.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._provider.shutdown()
        except Exception:  # noqa: BLE001
            pass
        if self._vad is not None:
            try:
                self._vad.shutdown()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Background work
    # ------------------------------------------------------------------

    def _preload(self) -> None:
        LOGGER.info("[STT] 開始背景載入模型...")
        try:
            self._provider.setup()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            LOGGER.warning("[STT] 模型載入失敗，STT 停用：%s", exc)
            self.availability_changed.emit(False)
            return
        LOGGER.info("[STT] 模型載入完成，STT 可用。")
        self.availability_changed.emit(True)

    def _run_session(self, session_id: int, stop_event: threading.Event) -> None:
        try:
            self._execute_session(session_id, stop_event)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("[STT] session %s 發生未預期例外", session_id)
            try:
                self._recorder.stop()
            except Exception:  # noqa: BLE001
                pass
            self._fail_session(session_id, "辨識發生未預期錯誤。")

    def _execute_session(self, session_id: int, stop_event: threading.Event) -> None:
        try:
            self._recorder.start()
        except MicrophoneError as exc:
            self._last_error = str(exc)
            LOGGER.warning("[STT] session %s 麥克風開啟失敗：%s", session_id, exc)
            self._fail_session(session_id, "無法使用麥克風。")
            return

        if not self._set_state_for_session(session_id, RecordingState.RECORDING):
            self._recorder.stop()
            return
        LOGGER.info("[STT] session %s 開始收音...", session_id)

        vad_cursor = 0
        session_vad = self._vad
        if session_vad is None:
            LOGGER.info("[STT] session %s VAD disabled", session_id)
        else:
            try:
                LOGGER.info("[STT] session %s VAD ready=%s", session_id, session_vad.is_ready())
                session_vad.reset()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("[STT] session %s VAD reset failed; continuing manually: %s", session_id, exc)
                session_vad = None

        # ponytail: 50ms 輪詢 stop/裝置失效/上限旗標，不用多 Event 等待器；
        # 若延遲敏感度提高再改事件驅動。
        # VAD 只在此 worker 推論，絕不可放進 PortAudio callback 以免阻塞收音。
        stop_reason = "manual"
        vad_endpoint_ts: float | None = None
        while not stop_event.wait(timeout=0.05):
            if self._recorder.device_failed.is_set():
                stop_reason = "device"
                break
            if self._recorder.max_reached.is_set():
                stop_reason = "max"
                break
            if session_vad is not None and session_vad.is_ready():
                try:
                    chunks, vad_cursor = self._recorder.read_new_chunks(vad_cursor)
                    if len(chunks) and session_vad.feed_audio(chunks):
                        stop_reason = "vad"
                        # Capture at the moment VAD actually declares the endpoint,
                        # not after the recorder-stop/buffer-read cleanup below.
                        vad_endpoint_ts = perf_counter()
                        break
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("[STT] session %s VAD failed; continuing manually: %s", session_id, exc)
                    session_vad = None

        if not self._set_state_for_session(session_id, RecordingState.STOPPING):
            self._recorder.stop()
            return
        self._recorder.stop()

        audio = self._recorder.get_audio()
        duration_ms = (len(audio) / self._sample_rate) * 1000.0 if self._sample_rate else 0.0
        LOGGER.info("[STT] session %s 停止收音 reason=%s samples=%s duration=%.0fms device_failed=%s", session_id, stop_reason, len(audio), duration_ms, self._recorder.device_failed.is_set())

        if len(audio) == 0 or duration_ms < self._min_recording_ms:
            self._discard_session(session_id, "錄音太短，請再試一次。")
            return

        if not self._set_state_for_session(session_id, RecordingState.TRANSCRIBING):
            return

        stt_started_ts = perf_counter()
        try:
            result = self._provider.transcribe(audio, self._sample_rate)
        except SttError as exc:
            self._last_error = str(exc)
            self._fail_session(session_id, "辨識失敗，請再試一次。")
            return

        stt_done_ts = perf_counter()
        LOGGER.info("[STT] language=%s p=%.2f duration=%.1fs stt=%.2fs", result.language, result.language_probability, result.audio_duration_seconds, stt_done_ts - stt_started_ts)

        text = result.text.strip()
        if _is_effectively_empty(text):
            self._discard_session(session_id, "沒有偵測到有效內容，請再試一次。")
            return

        self.voice_turn_timing.emit(stop_reason == "vad", vad_endpoint_ts or 0.0, stt_started_ts, stt_done_ts)
        self._submit(session_id, text)

    # ------------------------------------------------------------------
    # State transition helpers（集中管理，過期 session 結果一律略過）
    # ------------------------------------------------------------------

    def _set_state_for_session(self, session_id: int, new_state: RecordingState) -> bool:
        with self._state_lock:
            if self._shutting_down or session_id != self._session_id:
                return False
            if self._state == new_state:
                # 已處於目標狀態（例如 stop_session() 已先轉為 STOPPING），略過重複 emit。
                return True
            self._state = new_state
        self.state_changed.emit(new_state.value)
        return True

    def _discard_session(self, session_id: int, reason: str) -> None:
        LOGGER.info("[STT] session %s 捨棄：%s", session_id, reason)
        with self._state_lock:
            if self._shutting_down or session_id != self._session_id:
                return
        self.session_discarded.emit(reason)
        self._return_to_idle(session_id)

    def _fail_session(self, session_id: int, message: str) -> None:
        LOGGER.warning("[STT] session %s 失敗：%s（detail=%r）", session_id, message, self._last_error)
        with self._state_lock:
            if self._shutting_down or session_id != self._session_id:
                return
            self._state = RecordingState.ERROR
        self.state_changed.emit(RecordingState.ERROR.value)
        self.error_occurred.emit(message)
        self._return_to_idle(session_id)

    def _submit(self, session_id: int, text: str) -> None:
        with self._state_lock:
            if self._shutting_down or session_id != self._session_id or self._submitted:
                return
            self._submitted = True
            self._state = RecordingState.SUBMITTING
        self.state_changed.emit(RecordingState.SUBMITTING.value)
        self.transcript_ready.emit(text)
        self._return_to_idle(session_id)

    def _return_to_idle(self, session_id: int) -> None:
        with self._state_lock:
            if session_id != self._session_id:
                return
            self._state = RecordingState.IDLE
        self.state_changed.emit(RecordingState.IDLE.value)
