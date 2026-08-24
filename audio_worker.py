"""
ECHOES — 音訊播放佇列 Worker。

實作 Producer-Consumer 模式：
- Producer (TTS worker) 將取得的 BytesIO 放入 queue
- Consumer (AudioStreamWorker) 持續從 queue 取出並依序播放，消除句間停頓

Thread-Safety 設計要點：
- 使用 queue.Queue（內建 mutex）作為唯一共享狀態，避免手動加鎖
- 使用 threading.Thread(daemon=True) 而非 QThread，避免 Qt 父子關係
  在物件銷毀時觸發「QThread: Destroyed while thread is still running」abort
- stop() 送出 sentinel None，consumer loop 收到後正常結束
- _playing_lock 確保 is_busy() 的讀寫原子性
- 外部呼叫 enqueue() 與 stop() 均為 thread-safe（queue.Queue 操作）
- PyQt5 signal.emit() 在任意 thread 呼叫均為 thread-safe
"""

from __future__ import annotations

import io
import queue
import threading
import time

from PyQt5.QtCore import QObject, pyqtSignal

from audio_playback import FfplayPcmAudioPlayer, PlaybackStartSuppressed, PygameInMemoryAudioPlayer
from pet_harness.latency import get_turn

_SENTINEL = None
_PCM_STREAM_SENTINEL = object()


class _PcmTraceSession:
    def __init__(
        self,
        owner: "AudioStreamWorker",
        trace_id: str,
        session_reply_id: str,
        player,
        bytes_per_second: float,
    ):
        self._owner = owner
        self._trace_id = trace_id
        self._session_reply_id = session_reply_id
        self._player = player
        self._bytes_per_second = max(1.0, float(bytes_per_second or 1.0))
        self._chunk_queue: "queue.Queue[tuple[str, bytes] | object]" = queue.Queue()
        self._lock = threading.Lock()
        self._segment_bytes: dict[str, int] = {}
        self._consumed_segment_bytes: dict[str, int] = {}
        self._finalized_segments: list[str] = []
        self._emitted_segments: set[str] = set()
        self._scheduled_segments = 0
        self._scheduled_bytes = 0
        self._timers: dict[str, threading.Timer] = {}
        self._started_at: float | None = None
        self._closed = False
        self._aborted = False
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"AudioStreamWorkerPCM:{trace_id[:8]}",
        )
        self._thread.start()

    def enqueue_chunk(self, reply_id: str, chunk: bytes):
        if not chunk:
            return
        with self._lock:
            if self._closed or self._aborted:
                return
            self._segment_bytes[reply_id] = self._segment_bytes.get(reply_id, 0) + len(chunk)
        self._chunk_queue.put((reply_id, bytes(chunk)))

    def finish_segment(self, reply_id: str):
        with self._lock:
            if self._aborted or reply_id in self._finalized_segments:
                return
            self._finalized_segments.append(reply_id)
            self._schedule_pending_segments_locked()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._chunk_queue.put(_PCM_STREAM_SENTINEL)

    def interrupt(self):
        with self._lock:
            if self._aborted:
                return
            self._aborted = True
            self._closed = True
            self._cancel_timers_locked()
        while not self._chunk_queue.empty():
            try:
                self._chunk_queue.get_nowait()
            except queue.Empty:
                break
        self._chunk_queue.put(_PCM_STREAM_SENTINEL)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _iter_chunks(self):
        while True:
            item = self._chunk_queue.get()
            if item is _PCM_STREAM_SENTINEL:
                return
            reply_id, chunk = item
            yield chunk
            with self._lock:
                if self._aborted:
                    continue
                self._consumed_segment_bytes[reply_id] = self._consumed_segment_bytes.get(reply_id, 0) + len(chunk)
                self._schedule_pending_segments_locked()

    def _before_start(self):
        if self._owner._is_trace_suppressed(self._trace_id):
            return False
        with self._lock:
            self._started_at = time.monotonic()
            self._schedule_pending_segments_locked()
        self._owner.driver_started.emit(self._session_reply_id, self._trace_id)
        timeline = get_turn(self._trace_id)
        if timeline is not None:
            timeline.mark("audio_play_started")
            timeline.log_current()
        self._owner.playback_started.emit(self._session_reply_id, self._trace_id)
        return True

    def _schedule_pending_segments_locked(self):
        if self._started_at is None or self._aborted:
            return
        while self._scheduled_segments < len(self._finalized_segments):
            reply_id = self._finalized_segments[self._scheduled_segments]
            total_bytes = int(self._segment_bytes.get(reply_id, 0) or 0)
            consumed_bytes = int(self._consumed_segment_bytes.get(reply_id, 0) or 0)
            if consumed_bytes < total_bytes:
                return
            self._scheduled_bytes += total_bytes
            end_time = self._started_at + (self._scheduled_bytes / self._bytes_per_second)
            delay = max(0.0, end_time - time.monotonic())
            timer = threading.Timer(delay, self._emit_segment_finished, args=(reply_id,))
            timer.daemon = True
            self._timers[reply_id] = timer
            timer.start()
            self._scheduled_segments += 1

    def _emit_segment_finished(self, reply_id: str):
        with self._lock:
            self._timers.pop(reply_id, None)
            if self._aborted or reply_id in self._emitted_segments:
                return
            self._emitted_segments.add(reply_id)
        self._owner.playback_finished.emit(reply_id, self._trace_id)

    def _cancel_timers_locked(self):
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()

    def _run(self):
        pending_reply_ids: list[str] = []
        try:
            self._player.play_chunks(self._iter_chunks(), before_start=self._before_start)
        except PlaybackStartSuppressed:
            pass
        except Exception as exc:  # pragma: no cover
            print(f"[AudioStreamWorker] PCM session 播放失敗 trace={self._trace_id}: {exc}")
        finally:
            with self._lock:
                if self._aborted:
                    self._cancel_timers_locked()
                else:
                    pending_timers = list(self._timers.values())
                    pending_reply_ids = list(self._timers.keys())
                    for timer in pending_timers:
                        timer.cancel()
                    self._timers.clear()
            for reply_id in pending_reply_ids:
                self._emit_segment_finished(reply_id)
            self._owner._on_pcm_session_finished(self._trace_id)


class AudioStreamWorker(QObject):
    """Consumer，依 FIFO 順序播放音訊，避免句間停頓。

    使用 daemon threading.Thread 而非 QThread，確保在物件銷毀時不觸發
    Qt 的 'QThread: Destroyed while still running' 致命錯誤。
    """

    driver_started = pyqtSignal(str, str)     # reply_id, trace_id
    playback_started = pyqtSignal(str, str)   # legacy alias: reply_id, trace_id
    playback_finished = pyqtSignal(str, str)  # reply_id, trace_id
    queue_drained = pyqtSignal()         # 佇列清空（TTS 全部播完）

    def __init__(
        self,
        audio_player=None,
        pcm_player_factory=None,
        pcm_sample_rate: int = 32000,
        pcm_channels: int = 1,
        pcm_session_idle_ms: int = 250,
        parent=None,
    ):
        super().__init__(parent)
        self._queue: queue.Queue[tuple[io.BytesIO, str, str] | None] = queue.Queue()
        self._player = audio_player or PygameInMemoryAudioPlayer()
        self._pcm_player_factory = pcm_player_factory or (
            lambda sample_rate, channels: FfplayPcmAudioPlayer(sample_rate=sample_rate, channels=channels)
        )
        self._pcm_sample_rate = int(pcm_sample_rate)
        self._pcm_channels = int(pcm_channels)
        self._pcm_bytes_per_second = max(1, self._pcm_sample_rate * self._pcm_channels * 2)
        self._pcm_session_idle_ms = max(0, int(pcm_session_idle_ms))
        self._playing_lock = threading.Lock()
        self._pcm_lock = threading.Lock()
        self._current_reply_id: str | None = None
        self._suppressed_traces: set[str] = set()
        self._pcm_sessions: dict[str, _PcmTraceSession] = {}
        # daemon=True：主程式退出時此 thread 自動終止，不觸發 Qt 的 QThread abort
        self._thread = threading.Thread(target=self._run, daemon=True, name="AudioStreamWorker")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """啟動 consumer thread。"""
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        """通知 Worker 在播完當前項目後退出。可從任意 thread 呼叫。"""
        with self._pcm_lock:
            sessions = list(self._pcm_sessions.values())
        for session in sessions:
            session.close()
        self._queue.put(_SENTINEL)

    def wait(self, timeout_ms: int = 5000) -> bool:
        """等待 consumer thread 結束，回傳是否在 timeout 內完成。"""
        timeout_s = max(0.0, timeout_ms / 1000.0)
        self._thread.join(timeout=timeout_s)
        return not self._thread.is_alive()

    def isRunning(self) -> bool:  # noqa: N802 - 保持與 QThread 同名介面
        return self._thread.is_alive()

    # ------------------------------------------------------------------
    # Public API（均為 Thread-Safe）
    # ------------------------------------------------------------------

    def enqueue(self, audio_bytes: io.BytesIO, reply_id: str, trace_id: str = "") -> None:
        """將一段音訊放入播放佇列。可在任意 Thread 呼叫。"""
        self._queue.put((audio_bytes, reply_id, str(trace_id)))

    def enqueue_pcm_chunk(
        self,
        chunk: bytes,
        reply_id: str,
        trace_id: str = "",
        sample_rate: int | None = None,
    ) -> None:
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            raise ValueError("PCM session playback requires a non-empty trace_id")
        timeline = get_turn(normalized_trace_id)
        if timeline is not None:
            timeline.mark("tts_first_pcm")
        with self._pcm_lock:
            session = self._pcm_sessions.get(normalized_trace_id)
            if session is None:
                session_sample_rate = int(sample_rate or self._pcm_sample_rate)
                session = _PcmTraceSession(
                    self,
                    normalized_trace_id,
                    reply_id,
                    self._pcm_player_factory(session_sample_rate, self._pcm_channels),
                    max(1, session_sample_rate * self._pcm_channels * 2),
                )
                self._pcm_sessions[normalized_trace_id] = session
        session.enqueue_chunk(reply_id, chunk)

    def finish_pcm_segment(self, reply_id: str, trace_id: str = "") -> None:
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        with self._pcm_lock:
            session = self._pcm_sessions.get(normalized_trace_id)
        if session is not None:
            session.finish_segment(reply_id)

    def close_trace_session(self, trace_id: str | None) -> None:
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        with self._pcm_lock:
            session = self._pcm_sessions.get(normalized_trace_id)
        if session is not None:
            session.close()

    def interrupt_trace(self, trace_id: str | None) -> None:
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        with self._pcm_lock:
            session = self._pcm_sessions.get(normalized_trace_id)
        if session is not None:
            session.interrupt()

    def clear_queue(self) -> None:
        """清空尚未播放的佇列項目（不中斷當前正在播放的音訊）。"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def interrupt_all(self) -> None:
        with self._pcm_lock:
            sessions = list(self._pcm_sessions.values())
        for session in sessions:
            session.interrupt()
        self.clear_queue()
        stop = getattr(self._player, "stop", None)
        if callable(stop):
            stop()

    def suppress_trace(self, trace_id: str | None) -> None:
        normalized = str(trace_id or "").strip()
        if not normalized:
            return
        self._suppressed_traces.add(normalized)
        self.interrupt_trace(normalized)

    def clear_suppressed_trace(self, trace_id: str | None) -> None:
        normalized = str(trace_id or "").strip()
        if not normalized:
            return
        self._suppressed_traces.discard(normalized)

    def is_busy(self) -> bool:
        """回傳是否正在播放或佇列中仍有待播項目。"""
        with self._playing_lock:
            has_current = self._current_reply_id is not None
        with self._pcm_lock:
            has_pcm_session = any(session.is_alive() for session in self._pcm_sessions.values())
        return has_current or has_pcm_session or not self._queue.empty()

    def _is_trace_suppressed(self, trace_id: str) -> bool:
        return trace_id in self._suppressed_traces

    def _on_pcm_session_finished(self, trace_id: str):
        with self._pcm_lock:
            self._pcm_sessions.pop(trace_id, None)
        self._emit_queue_drained_if_idle()

    def _emit_queue_drained_if_idle(self):
        with self._playing_lock:
            has_current = self._current_reply_id is not None
        with self._pcm_lock:
            has_pcm_session = any(session.is_alive() for session in self._pcm_sessions.values())
        if not has_current and not has_pcm_session and self._queue.empty():
            self.queue_drained.emit()

    def _play_buffer(self, audio_bytes: io.BytesIO, before_start):
        play = getattr(self._player, "play", None)
        if not callable(play):
            raise RuntimeError("音訊播放器缺少 play() 介面。")
        try:
            play(audio_bytes, before_start=before_start)
        except TypeError:
            if callable(before_start) and before_start() is False:
                raise PlaybackStartSuppressed("記憶體音訊在起播前被抑制。")
            play(audio_bytes)

    # ------------------------------------------------------------------
    # Consumer loop（在 daemon thread 中執行）
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            # 阻塞等待，直到有新項目或 sentinel；不使用 timeout 避免 busy-wait
            item = self._queue.get()

            if item is _SENTINEL:
                return

            audio_bytes, reply_id, trace_id = item

            with self._playing_lock:
                self._current_reply_id = reply_id

            try:
                if trace_id and trace_id in self._suppressed_traces:
                    raise PlaybackStartSuppressed("該 trace 已在起播前被抑制。")

                def before_start():
                    if trace_id and trace_id in self._suppressed_traces:
                        return False
                    self.driver_started.emit(reply_id, trace_id)
                    timeline = get_turn(trace_id)
                    if timeline is not None:
                        timeline.mark("audio_play_started")
                        timeline.log_current()
                    self.playback_started.emit(reply_id, trace_id)
                    return True

                self._play_buffer(audio_bytes, before_start)
                self.playback_finished.emit(reply_id, trace_id)
            except PlaybackStartSuppressed:
                pass
            except Exception as exc:  # pragma: no cover
                print(f"[AudioStreamWorker] 播放失敗 reply_id={reply_id}: {exc}")
            finally:
                with self._playing_lock:
                    self._current_reply_id = None

            self._emit_queue_drained_if_idle()
