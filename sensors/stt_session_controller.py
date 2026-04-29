from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal

from sensors.microphone_stt import AzureSTTWorker


def _log_session(message: str):
    print(f"[ECHOES][STT][CTRL] {message}")


class STTSessionController(QObject):
    """集中管理 Azure STT worker 的開始、停止與清理。"""

    recognized_text = pyqtSignal(str)
    warning_emitted = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    session_state_changed = pyqtSignal(bool)

    def __init__(self, worker_factory=AzureSTTWorker, parent=None):
        super().__init__(parent)
        self._worker_factory = worker_factory if callable(worker_factory) else AzureSTTWorker
        self._worker = None
        self._listening = False

    def is_listening(self) -> bool:
        return self._listening

    def start_session(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            self.status_changed.emit("Azure STT 已在收音中。")
            _log_session("略過 start：目前已有活動中的 STT session。")
            return False

        worker = self._worker_factory(parent=self)
        self._worker = worker
        worker.recognized_text.connect(self.recognized_text.emit)
        worker.warning_emitted.connect(self._handle_worker_warning)
        worker.status_changed.connect(self.status_changed.emit)
        if hasattr(worker, "listening_state_changed"):
            worker.listening_state_changed.connect(self._handle_listening_state_changed)
        worker.finished.connect(self._handle_worker_finished)

        self.status_changed.emit("正在啟動 STT 收音...")
        _log_session("建立新的 Azure STT worker 並開始啟動。")
        worker.start()
        return True

    def stop_session(self) -> bool:
        worker = self._worker
        if worker is None:
            self.status_changed.emit("Azure STT 目前未啟動。")
            _log_session("略過 stop：目前沒有活動中的 worker。")
            return False

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
            return
        _log_session("應用程式關閉中，準備清理 STT worker。")
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

    def _handle_worker_warning(self, message: str):
        self.warning_emitted.emit(message)

    def _handle_listening_state_changed(self, active: bool):
        active = bool(active)
        if self._listening == active:
            return
        self._listening = active
        self.session_state_changed.emit(active)
        _log_session(f"session_state_changed -> {active}")

    def _handle_worker_finished(self):
        worker = self._worker
        if worker is not None and hasattr(worker, "deleteLater"):
            worker.deleteLater()
        self._worker = None
        if self._listening:
            self._listening = False
            self.session_state_changed.emit(False)
        self.status_changed.emit("STT 收音已停止，等待下一次開始。")
        _log_session("STT worker 已結束並完成清理。")
