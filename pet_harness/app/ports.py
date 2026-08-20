from abc import ABC, abstractmethod
from threading import Lock
from typing import Any, Callable


class BackgroundExecutor(ABC):
    @abstractmethod
    def submit(self, job: Callable[[], Any], on_done: Callable[[bool, str, Any], None]) -> None: ...


class PreparedTurn:
    """A queued turn that releases its captured engine lease exactly once."""

    def __init__(self, run: Callable[[], dict[str, Any]], release: Callable[[], None], cancel: Callable[[], None] | None = None) -> None:
        self._run = run
        self._release = release
        self._cancel = cancel or (lambda: None)
        self._released = False
        self._lock = Lock()

    def __call__(self) -> dict[str, Any]:
        try:
            return self._run()
        finally:
            self.release()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._release()

    def cancel(self) -> None:
        self._cancel()
