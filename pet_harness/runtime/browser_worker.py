from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from pet_harness.runtime.base_browser_runtime import BrowserCommand, BrowserCommandResult


class BrowserWorker:
    """A single owner thread for synchronous Playwright objects."""

    def __init__(self, handler: Callable[[BrowserCommand], BrowserCommandResult]) -> None:
        self._handler = handler
        self._queue: queue.Queue[tuple[BrowserCommand, threading.Event, dict] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def submit(self, command: BrowserCommand, timeout_seconds: float) -> BrowserCommandResult:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="browser-worker", daemon=True)
            self._thread.start()
        done, slot = threading.Event(), {}
        self._queue.put((command, done, slot))
        if not done.wait(timeout_seconds):
            return BrowserCommandResult("failed", error={"reason": "timeout", "message": "Browser command timed out", "retryable": True})
        return slot["result"]

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout_seconds)
        self._thread = None

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._stopped.set()
                return
            command, done, slot = item
            try:
                result = self._handler(command)
            except Exception as exc:  # browser failures must stay inside the worker boundary.
                result = BrowserCommandResult("failed", error={"reason": "browser_error", "message": str(exc), "retryable": False})
            if not done.is_set():
                slot["result"] = result
                done.set()
