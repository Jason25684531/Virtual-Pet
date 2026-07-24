"""Qt adapter for the application BackgroundExecutor port."""

from __future__ import annotations

import logging
import time
from threading import RLock
from typing import Any, Callable

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from pet_harness.app.ports.background_executor import BackgroundExecutor


LOGGER = logging.getLogger(__name__)


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
        self._lock = RLock()
        self._accepting = True

    @property
    def name(self) -> str:
        return "conversation_executor"

    def start(self) -> None:
        return None

    def submit(self, job: Callable[[], Any], on_done: Callable[[bool, str, Any], None]) -> None:
        with self._lock:
            if not self._accepting:
                raise RuntimeError("conversation executor is shutting down")
            worker = _JobThread(job)
            self._jobs[worker] = on_done
            worker.completed.connect(self._complete)
            worker.start()

    def shutdown(self, wait_ms: int = 5000) -> None:
        self.stop(wait_ms)

    def stop(self, wait_ms: int = 5000) -> None:
        """Reject new jobs and wait only until one shared shutdown deadline."""
        with self._lock:
            self._accepting = False
            workers = tuple(self._jobs)
        deadline = time.monotonic() + max(0, wait_ms) / 1000
        for worker in workers:
            if not worker.isRunning():
                continue
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if not worker.wait(remaining_ms):
                LOGGER.warning("conversation worker still running after shutdown timeout")

    def _complete(self, worker: _JobThread, ok: bool, message: str, payload: Any) -> None:
        with self._lock:
            callback = self._jobs.pop(worker, None)
        if callback is not None:
            callback(ok, message, payload)
        worker.deleteLater()


BackgroundExecutor.register(QtBackgroundExecutor)
