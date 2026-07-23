from abc import ABC, abstractmethod
from typing import Any, Callable


class BackgroundExecutor(ABC):
    @abstractmethod
    def submit(self, job: Callable[[], Any], on_done: Callable[[bool, str, Any], None]) -> None: ...
