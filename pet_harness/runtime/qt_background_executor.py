"""Qt adapter for the application BackgroundExecutor port."""

from __future__ import annotations

import logging
import time
from threading import RLock
from typing import Any, Callable

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from pet_harness.app.ports import BackgroundExecutor


LOGGER = logging.getLogger(__name__)


class _JobThread(QThread):
    def __init__(self, job: Callable[[], Any], completed) -> None:
        super().__init__()
        self._job = job
        self._completed = completed

    def run(self) -> None:
        try:
            self._completed.emit(self, True, "", self._job())
        except Exception as exc:  # callback carries failures without killing Qt's loop
            LOGGER.warning("background job failed: %s", exc, exc_info=True)
            self._completed.emit(self, False, str(exc), None)
        except BaseException as exc:  # GreenletExit 之類不是 Exception 的子類；沒接住就會靜默消失、callback 永遠不來
            LOGGER.error("background job aborted: %r", exc, exc_info=True)
            self._completed.emit(self, False, repr(exc), None)


class QtBackgroundExecutor(QObject):
    """BackgroundExecutor 的 Qt 實作。

    不能同時繼承 QObject 與 ABC（sip metaclass 與 ABCMeta 衝突），
    改用虛擬子類註冊維持 isinstance(executor, BackgroundExecutor) 成立；
    QObject 必須保留：completed 訊號經 queued connection 才會把 on_done
    排回 UI 執行緒。"""

    # 完成訊號掛在 executor 上、只在建構它的主執行緒連一次。不能每個 job 各自 connect：
    # submit() 常在另一個 job 的執行緒裡被呼叫（例如對話回合內再送出慢速工具），Nuitka 編譯後
    # PyQt 會為 slot 建立 proxy 並綁在呼叫 connect 的執行緒，那條執行緒一結束 callback 就永遠不會到。
    _job_done = pyqtSignal(object, bool, str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs: dict[_JobThread, Callable[[bool, str, Any], None]] = {}
        self._job_done.connect(self._complete)
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
            worker = _JobThread(job, self._job_done)
            self._jobs[worker] = on_done
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
        try:
            if callback is not None:
                callback(ok, message, payload)
        except Exception:  # callback 失敗不可吞掉：否則工具結果與 UI 狀態會無聲遺失
            LOGGER.exception("background job callback failed")
        finally:
            worker.deleteLater()


BackgroundExecutor.register(QtBackgroundExecutor)
