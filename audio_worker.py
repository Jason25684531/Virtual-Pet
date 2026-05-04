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

from PyQt5.QtCore import QObject, pyqtSignal

from audio_playback import PygameInMemoryAudioPlayer

_SENTINEL = None


class AudioStreamWorker(QObject):
    """Consumer，依 FIFO 順序播放音訊，避免句間停頓。

    使用 daemon threading.Thread 而非 QThread，確保在物件銷毀時不觸發
    Qt 的 'QThread: Destroyed while still running' 致命錯誤。
    """

    playback_started = pyqtSignal(str, str)   # reply_id, trace_id
    playback_finished = pyqtSignal(str, str)  # reply_id, trace_id
    queue_drained = pyqtSignal()         # 佇列清空（TTS 全部播完）

    def __init__(self, audio_player=None, parent=None):
        super().__init__(parent)
        self._queue: queue.Queue[tuple[io.BytesIO, str, str] | None] = queue.Queue()
        self._player = audio_player or PygameInMemoryAudioPlayer()
        self._playing_lock = threading.Lock()
        self._current_reply_id: str | None = None
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

    def clear_queue(self) -> None:
        """清空尚未播放的佇列項目（不中斷當前正在播放的音訊）。"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def is_busy(self) -> bool:
        """回傳是否正在播放或佇列中仍有待播項目。"""
        with self._playing_lock:
            has_current = self._current_reply_id is not None
        return has_current or not self._queue.empty()

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
                self.playback_started.emit(reply_id, trace_id)
                self._player.play(audio_bytes)
                self.playback_finished.emit(reply_id, trace_id)
            except Exception as exc:  # pragma: no cover
                print(f"[AudioStreamWorker] 播放失敗 reply_id={reply_id}: {exc}")
            finally:
                with self._playing_lock:
                    self._current_reply_id = None

            if self._queue.empty():
                self.queue_drained.emit()
