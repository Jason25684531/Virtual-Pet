from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal

from interaction_trace import InteractionLatencyTracker
from sensors.microphone_stt import AzureSTTWorker


def _log_session(message: str):
    print(f"[ECHOES][STT][CTRL] {message}")


class STTSessionController(QObject):
    """集中管理 Azure STT worker 的開始、停止與清理。"""

    STATE_IDLE = "idle"
    STATE_STARTING = "starting"
    STATE_LISTENING = "listening"
    STATE_STOPPING = "stopping"
    STATE_UNAVAILABLE = "unavailable"
    VALID_STATES = {
        STATE_IDLE,
        STATE_STARTING,
        STATE_LISTENING,
        STATE_STOPPING,
        STATE_UNAVAILABLE,
    }

    speech_started = pyqtSignal(object)
    speech_ended = pyqtSignal(object)
    recognizing_text = pyqtSignal(str)
    recognized_text = pyqtSignal(str)
    recognized_result = pyqtSignal(str, object)
    warning_emitted = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    session_state_changed = pyqtSignal(bool)
    session_lifecycle_changed = pyqtSignal(str)

    def __init__(
        self,
        worker_factory=AzureSTTWorker,
        latency_tracker: InteractionLatencyTracker | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._worker_factory = worker_factory if callable(worker_factory) else AzureSTTWorker
        self._latency_tracker = latency_tracker
        self._worker = None
        self._listening = False
        self._state = self.STATE_IDLE
        self._active_trace_id: str | None = None

    def is_listening(self) -> bool:
        return self._listening

    def state(self) -> str:
        return self._state

    def start_session(self) -> bool:
        if self._state in {self.STATE_STARTING, self.STATE_STOPPING}:
            self.status_changed.emit("Azure STT 正在切換狀態，請稍候。")
            _log_session(f"略過 start：目前 state={self._state}。")
            return False
        if self._worker is not None and self._worker.isRunning():
            self.status_changed.emit("Azure STT 已在收音中。")
            _log_session("略過 start：目前已有活動中的 STT session。")
            return False

        worker = self._worker_factory(parent=self)
        self._worker = worker
        if hasattr(worker, "speech_started"):
            worker.speech_started.connect(self._handle_speech_started)
        if hasattr(worker, "speech_ended"):
            worker.speech_ended.connect(self._handle_speech_ended)
        if hasattr(worker, "recognizing_text"):
            worker.recognizing_text.connect(self.recognizing_text.emit)
        worker.recognized_text.connect(self._handle_recognized_text)
        worker.warning_emitted.connect(self._handle_worker_warning)
        worker.status_changed.connect(self.status_changed.emit)
        if hasattr(worker, "listening_state_changed"):
            worker.listening_state_changed.connect(self._handle_listening_state_changed)
        worker.finished.connect(self._handle_worker_finished)

        self._set_state(self.STATE_STARTING)
        self.status_changed.emit("正在啟動 STT 收音...")
        _log_session("建立新的 Azure STT worker 並開始啟動。")
        worker.start()
        return True

    def stop_session(self) -> bool:
        worker = self._worker
        if worker is None:
            self.status_changed.emit("Azure STT 目前未啟動。")
            _log_session("略過 stop：目前沒有活動中的 worker。")
            if self._state != self.STATE_UNAVAILABLE:
                self._set_state(self.STATE_IDLE)
            return False

        self._set_state(self.STATE_STOPPING)
        self.status_changed.emit("正在停止 STT 收音...")
        _log_session("收到停止收音請求。")
        try:
            worker.stop()
            worker.quit()
        except Exception as exc:
            warning = f"停止 Azure STT 時發生例外：{exc}"
            self.warning_emitted.emit(warning)
            _log_session(warning)
        return True

    def shutdown(self):
        worker = self._worker
        if worker is None:
            if self._state != self.STATE_UNAVAILABLE:
                self._set_state(self.STATE_IDLE)
            return
        _log_session("應用程式關閉中，準備清理 STT worker。")
        self._set_state(self.STATE_STOPPING)
        try:
            worker.stop()
            worker.quit()
            if worker.isRunning():
                worker.wait(5000)
        finally:
            self._worker = None
            if self._listening:
                self._listening = False
                self.session_state_changed.emit(False)
            if self._state != self.STATE_UNAVAILABLE:
                self._set_state(self.STATE_IDLE)

    def _handle_worker_warning(self, message: str):
        self.warning_emitted.emit(message)

    def _ensure_trace(self) -> str | None:
        if self._latency_tracker is None:
            return None
        if self._active_trace_id:
            return self._active_trace_id
        self._active_trace_id = self._latency_tracker.begin_interaction("stt", "(等待 STT finalized)")
        return self._active_trace_id

    def _clear_trace_if_pending(self, reason: str):
        if not self._active_trace_id or self._latency_tracker is None:
            self._active_trace_id = None
            return
        snapshot = self._latency_tracker.snapshot(self._active_trace_id)
        if snapshot is not None:
            self._latency_tracker.abort(self._active_trace_id, reason)
        self._active_trace_id = None

    def _handle_speech_started(self):
        trace_id = self._ensure_trace()
        if trace_id and self._latency_tracker is not None:
            self._latency_tracker.mark_stt_speech_started(trace_id)
        self.speech_started.emit(trace_id)

    def _handle_speech_ended(self):
        trace_id = self._ensure_trace()
        if trace_id and self._latency_tracker is not None:
            self._latency_tracker.mark_stt_speech_ended(trace_id)
        self.speech_ended.emit(trace_id)

    def _handle_recognized_text(self, text: str):
        trace_id = self._ensure_trace()
        if trace_id and self._latency_tracker is not None:
            self._latency_tracker.mark_stt_finalized(trace_id, text)
        self.recognized_text.emit(text)
        self.recognized_result.emit(text, trace_id)
        self._active_trace_id = None

    def _handle_listening_state_changed(self, active: bool):
        active = bool(active)
        if self._listening == active:
            return
        self._listening = active
        self.session_state_changed.emit(active)
        if active:
            self._set_state(self.STATE_LISTENING)
        elif self._state != self.STATE_STOPPING:
            if self._worker is not None and self._worker.isRunning():
                self._set_state(self.STATE_STOPPING)
            else:
                self._set_state(self.STATE_IDLE)
        _log_session(f"session_state_changed -> {active}")

    def _handle_worker_finished(self):
        worker = self._worker
        if worker is not None and hasattr(worker, "deleteLater"):
            worker.deleteLater()
        self._worker = None
        self._clear_trace_if_pending("STT worker 結束前未取得 finalized 文字")
        if self._listening:
            self._listening = False
            self.session_state_changed.emit(False)
        if self._state != self.STATE_UNAVAILABLE:
            self._set_state(self.STATE_IDLE)
        self.status_changed.emit("STT 收音已停止，等待下一次開始。")
        _log_session("STT worker 已結束並完成清理。")

    def _set_state(self, state: str):
        if state not in self.VALID_STATES:
            raise ValueError(f"Unknown STT lifecycle state: {state}")
        if self._state == state:
            return
        self._state = state
        self.session_lifecycle_changed.emit(state)
        _log_session(f"session_lifecycle_changed -> {state}")
