from abc import ABC, abstractmethod
from threading import Lock
from typing import Any, Callable


class PreparedTurn:
    """A queued turn that releases its captured engine lease exactly once."""

    def __init__(self, run: Callable[[], dict[str, Any]], release: Callable[[], None]) -> None:
        self._run = run
        self._release = release
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


class ConversationPort(ABC):
    @abstractmethod
    def prepare_turn(self, text: str, source: str, character_id: str) -> PreparedTurn: ...
