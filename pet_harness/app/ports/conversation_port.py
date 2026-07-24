from abc import ABC, abstractmethod
from typing import Any, Callable


class ConversationPort(ABC):
    @abstractmethod
    def prepare_turn(self, text: str, source: str, character_id: str) -> Callable[[], dict[str, Any]]: ...
