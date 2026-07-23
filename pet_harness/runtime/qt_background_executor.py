"""Qt adapter for the application BackgroundExecutor port."""

from __future__ import annotations

from typing import Any, Callable

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from pet_harness.app.ports.background_executor import BackgroundExecutor


class _JobThread(QThread):
    completed = pyqtSignal(object, bool, str, object)

    def __init__(self, job: Callable[[], Any]) -> None:
        super().__init__()
        self._job = job

    def run(self) -> None:
        try:
            self.completed.emit(self, True, "", self._job())
        except Exception as exc:  # callback carries failures without killing Qt's loop
            self.completed.emit(self, False, str(exc), None)


class QtBackgroundExecutor(QObject):
    """BackgroundExecutor 的 Qt 實作。

    不能同時繼承 QObject 與 ABC（sip metaclass 與 ABCMeta 衝突），
    改用虛擬子類註冊維持 isinstance(executor, BackgroundExecutor) 成立；
    QObject 必須保留：completed 訊號經 queued connection 才會把 on_done
    排回 UI 執行緒。"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs: dict[_JobThread, Callable[[bool, str, Any], None]] = {}

    def submit(self, job: Callable[[], Any], on_done: Callable[[bool, str, Any], None]) -> None:
        worker = _JobThread(job)
        self._jobs[worker] = on_done
        worker.completed.connect(self._complete)
        worker.start()

    def _complete(self, worker: _JobThread, ok: bool, message: str, payload: Any) -> None:
        callback = self._jobs.pop(worker, None)
        if callback is not None:
            callback(ok, message, payload)
        worker.deleteLater()


BackgroundExecutor.register(QtBackgroundExecutor)
